"""
Media Downloader — Telethon
=============================
Downloads media from parsed posts and attaches either the file itself
or a direct link to it, depending on file size and configuration.

Usage:
    from media_downloader_telethon import download_and_attach

    # Inside your NewMessage handler:
    result = await download_and_attach(client, message, parsed_post)
    # result.file_path   — local path (if downloaded)
    # result.public_link — t.me link  (if available)
    # result.method      — "file" | "link" | "both" | "none"
"""

from __future__ import annotations

import logging
import mimetypes
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from telethon import TelegramClient
from telethon.tl.types import (
    Document,
    MessageMediaContact,
    MessageMediaDocument,
    MessageMediaGeo,
    MessageMediaGeoLive,
    MessageMediaPhoto,
    MessageMediaPoll,
    MessageMediaVenue,
    MessageMediaWebPage,
    Photo,
)

logger = logging.getLogger("media_dl_telethon")

# ═══════════════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════════════

# Directory where downloaded media is stored
DOWNLOAD_DIR: str = os.getenv("DOWNLOAD_DIR", "downloads")

# Max file size to auto-download (bytes).  Files larger than this get a
# link only.  Default 50 MB.  Set 0 to disable the limit.
MAX_DOWNLOAD_SIZE: int = int(os.getenv("MAX_DOWNLOAD_SIZE", str(50 * 1024 * 1024)))

# When sending to log chat: "file" = always send file, "link" = always
# send link, "auto" = send file if small enough, otherwise link,
# "both" = send file + link when possible.
ATTACH_MODE: str = os.getenv("ATTACH_MODE", "auto")   # file | link | auto | both

# Keep files on disk after sending?  "true" / "false"
KEEP_FILES: bool = os.getenv("KEEP_FILES", "true").lower() in ("1", "true", "yes")


# ═══════════════════════════════════════════════════════════════════════════
# Result dataclass
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class MediaResult:
    """Result of a media download / attach operation."""

    method: str = "none"                 # "file" | "link" | "both" | "none"
    file_path: Optional[str] = None      # local path to downloaded file
    file_size: int = 0
    file_name: str = ""
    mime_type: str = ""
    public_link: str = ""                # https://t.me/…  (public channels only)
    media_type: str = ""                 # photo | video | document | audio | …
    error: str = ""
    duration_ms: int = 0                 # how long the download took


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _ensure_dir(path: str) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


def _build_public_link(chat_username: str, message_id: int) -> str:
    """Build a t.me link to the original message (public channels only)."""
    if chat_username:
        return f"https://t.me/{chat_username}/{message_id}"
    return ""


def _get_media_meta(message) -> tuple[str, int, str]:
    """Return (media_type, approx_file_size, suggested_filename)."""
    media = message.media
    if media is None:
        return ("", 0, "")

    if isinstance(media, MessageMediaPhoto):
        # Photo sizes are sorted smallest → largest; take the biggest
        photo: Photo = media.photo
        if photo and photo.sizes:
            # Estimate size from the largest PhotoSize
            largest = photo.sizes[-1]
            size = getattr(largest, "size", 0) or 0
        else:
            size = 0
        return ("photo", size, "")

    if isinstance(media, MessageMediaDocument):
        doc: Document = media.document
        if doc is None:
            return ("document", 0, "")

        size = doc.size or 0
        mime = doc.mime_type or ""
        fname = ""

        for attr in doc.attributes:
            if hasattr(attr, "file_name") and attr.file_name:
                fname = attr.file_name
                break

        # Classify
        is_animated = any(
            type(a).__name__ == "DocumentAttributeAnimated" for a in doc.attributes
        )
        is_sticker = any(
            type(a).__name__ == "DocumentAttributeSticker" for a in doc.attributes
        )
        is_video = any(
            type(a).__name__ == "DocumentAttributeVideo" for a in doc.attributes
        )
        is_audio = any(
            type(a).__name__ == "DocumentAttributeAudio" for a in doc.attributes
        )

        if is_sticker:
            mtype = "sticker"
        elif is_animated:
            mtype = "gif"
        elif is_video:
            round_msg = any(
                getattr(a, "round_message", False)
                for a in doc.attributes
                if type(a).__name__ == "DocumentAttributeVideo"
            )
            mtype = "video_note" if round_msg else "video"
        elif is_audio:
            is_voice = any(
                getattr(a, "voice", False)
                for a in doc.attributes
                if type(a).__name__ == "DocumentAttributeAudio"
            )
            mtype = "voice" if is_voice else "audio"
        else:
            mtype = "document"

        if not fname:
            ext = mimetypes.guess_extension(mime) or ""
            fname = f"{mtype}_{message.id}{ext}"

        return (mtype, size, fname)

    if isinstance(media, MessageMediaContact):
        return ("contact", 0, "")
    if isinstance(media, (MessageMediaGeo, MessageMediaGeoLive)):
        return ("location", 0, "")
    if isinstance(media, MessageMediaPoll):
        return ("poll", 0, "")
    if isinstance(media, MessageMediaVenue):
        return ("venue", 0, "")
    if isinstance(media, MessageMediaWebPage):
        return ("webpage", 0, "")

    return ("other", 0, "")


# ═══════════════════════════════════════════════════════════════════════════
# Core: download media from a message
# ═══════════════════════════════════════════════════════════════════════════

async def download_media(
    client: TelegramClient,
    message,
    *,
    dest_dir: str = "",
    size_limit: int = 0,
) -> MediaResult:
    """
    Download media from a Telethon message.

    Parameters
    ----------
    client : TelegramClient
    message : telethon Message object
    dest_dir : target directory (default DOWNLOAD_DIR)
    size_limit : skip download if file exceeds this (0 = no limit)

    Returns
    -------
    MediaResult with file_path set on success.
    """
    dest_dir = dest_dir or DOWNLOAD_DIR
    _ensure_dir(dest_dir)

    result = MediaResult()
    media_type, approx_size, suggested_name = _get_media_meta(message)
    result.media_type = media_type

    if not media_type or media_type in ("contact", "location", "poll", "venue", "webpage"):
        result.method = "none"
        return result

    result.file_size = approx_size
    result.file_name = suggested_name

    # Build public link
    chat = await message.get_chat()
    chat_username = getattr(chat, "username", "") or ""
    result.public_link = _build_public_link(chat_username, message.id)

    # Check size limit
    effective_limit = size_limit or MAX_DOWNLOAD_SIZE
    if effective_limit and approx_size > effective_limit:
        logger.info(
            "Skipping download: %s (%s) exceeds limit %s",
            suggested_name, _fmt(approx_size), _fmt(effective_limit),
        )
        result.method = "link" if result.public_link else "none"
        return result

    # Download
    t0 = time.monotonic()
    try:
        path = await client.download_media(
            message,
            file=os.path.join(dest_dir, suggested_name) if suggested_name else dest_dir,
        )
    except Exception as exc:
        logger.error("Download failed: %s", exc)
        result.error = str(exc)
        result.method = "link" if result.public_link else "none"
        return result

    elapsed = int((time.monotonic() - t0) * 1000)
    result.duration_ms = elapsed

    if path:
        result.file_path = str(path)
        result.file_size = os.path.getsize(path)
        result.mime_type = mimetypes.guess_type(path)[0] or ""
        result.file_name = os.path.basename(path)
        logger.info(
            "Downloaded %s → %s (%s) in %dms",
            media_type, path, _fmt(result.file_size), elapsed,
        )
    else:
        result.error = "download_media returned None"
        result.method = "link" if result.public_link else "none"
        return result

    # Decide method
    if result.file_path and result.public_link:
        result.method = "both"
    elif result.file_path:
        result.method = "file"
    elif result.public_link:
        result.method = "link"
    else:
        result.method = "none"

    return result


# ═══════════════════════════════════════════════════════════════════════════
# Core: download + send to log chat in one step
# ═══════════════════════════════════════════════════════════════════════════

async def download_and_attach(
    client: TelegramClient,
    message,
    parsed_html: str,
    log_chat: int | str,
    *,
    attach_mode: str = "",
) -> MediaResult:
    """
    Download media from *message*, then send it to *log_chat* alongside the
    parsed HTML summary.

    attach_mode
    -----------
    "file"  — always upload the file (skip if too large)
    "link"  — never upload; include link in the caption
    "auto"  — upload if within size limit, otherwise link  (default)
    "both"  — upload AND include link in caption

    Returns the MediaResult for inspection / logging.
    """
    mode = (attach_mode or ATTACH_MODE).lower()
    result = await download_media(client, message)
    if message.grouped_id:
        logger.debug("Message is part of a media group (album), skipping download to avoid duplicates.")
        result.method = "none"
        return result

    # ── nothing to attach ──
    if result.method == "none":
        await client.send_message(
            log_chat, parsed_html, parse_mode="html", link_preview=False,
        )
        return result

    # ── build caption (trimmed to 1024 for media messages) ──
    link_line = ""
    if result.public_link:
        from html import escape
        link_line = f'\n\n<a href="{escape(result.public_link)}">🔗 Original</a>'

    caption = parsed_html
    if len(caption) > 900:
        caption = caption[:900] + "…"

    # ── decide what to send ──
    send_file = False
    send_link_only = False

    if mode == "file":
        send_file = bool(result.file_path)
    elif mode == "link":
        send_link_only = True
    elif mode == "both":
        send_file = bool(result.file_path)
        if result.public_link:
            caption += link_line
    else:  # auto
        if result.file_path:
            send_file = True
        else:
            send_link_only = True

    # ── send ──
    try:
        if send_file and result.file_path:
            # Telethon auto-detects photo vs document based on mime
            force_document = result.media_type in (
                "document", "audio", "voice", "sticker",
            )
            await client.send_file(
                log_chat,
                result.file_path,
                caption=caption + (link_line if mode == "both" else ""),
                parse_mode="html",
                force_document=force_document,
            )
            result.method = "both" if (mode == "both" and result.public_link) else "file"
        elif send_link_only or not result.file_path:
            text = parsed_html
            if result.public_link:
                text += link_line
            # Append file info if we have metadata but didn't download
            if result.file_size and not result.file_path:
                from html import escape
                text += (
                    f"\n\n📎 <b>Media not downloaded</b> "
                    f"({escape(result.media_type)}, {_fmt(result.file_size)})"
                )
            await client.send_message(
                log_chat, text, parse_mode="html", link_preview=False,
            )
            result.method = "link" if result.public_link else "none"
        else:
            await client.send_message(
                log_chat, parsed_html, parse_mode="html", link_preview=False,
            )
    except Exception as exc:
        logger.error("Failed to send media to log chat: %s", exc)
        result.error = str(exc)
        # Fallback: send text only
        try:
            await client.send_message(
                log_chat, parsed_html, parse_mode="html", link_preview=False,
            )
        except Exception:
            pass

    # ── cleanup ──
    if result.file_path and not KEEP_FILES:
        try:
            os.remove(result.file_path)
            logger.debug("Removed %s", result.file_path)
        except OSError:
            pass

    return result


def _fmt(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024  # type: ignore[assignment]
    return f"{b:.1f} TB"
