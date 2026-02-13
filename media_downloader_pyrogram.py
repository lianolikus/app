"""
Media Downloader — Pyrogram
==============================
Downloads media from parsed posts and attaches either the file itself
or a direct link to it, depending on file size and configuration.

Usage:
    from media_downloader_pyrogram import download_and_attach

    # Inside your on_message handler:
    result = await download_and_attach(client, message, parsed_html, log_chat)
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

from pyrogram import Client
from pyrogram.types import Message

logger = logging.getLogger("media_dl_pyrogram")

# ═══════════════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════════════

DOWNLOAD_DIR: str = os.getenv("DOWNLOAD_DIR", "downloads")

# Max file size to auto-download (bytes).  Default 50 MB.  0 = no limit.
MAX_DOWNLOAD_SIZE: int = int(os.getenv("MAX_DOWNLOAD_SIZE", str(50 * 1024 * 1024)))

# "file" | "link" | "auto" | "both"
ATTACH_MODE: str = os.getenv("ATTACH_MODE", "auto")

KEEP_FILES: bool = os.getenv("KEEP_FILES", "true").lower() in ("1", "true", "yes")


# ═══════════════════════════════════════════════════════════════════════════
# Result dataclass
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class MediaResult:
    method: str = "none"                 # "file" | "link" | "both" | "none"
    file_path: Optional[str] = None
    file_size: int = 0
    file_name: str = ""
    mime_type: str = ""
    public_link: str = ""
    media_type: str = ""                 # photo | video | document | …
    error: str = ""
    duration_ms: int = 0


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _ensure_dir(path: str) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


def _build_link(username: str, msg_id: int) -> str:
    return f"https://t.me/{username}/{msg_id}" if username else ""


def _fmt(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024  # type: ignore[assignment]
    return f"{b:.1f} TB"


def _get_media_meta(msg: Message) -> tuple[str, int, str, str]:
    """Return (media_type, file_size, file_name, mime_type)."""

    if msg.photo:
        return ("photo", msg.photo.file_size or 0, "", "image/jpeg")

    if msg.video:
        return (
            "video",
            msg.video.file_size or 0,
            msg.video.file_name or f"video_{msg.id}.mp4",
            msg.video.mime_type or "video/mp4",
        )

    if msg.animation:
        return (
            "gif",
            msg.animation.file_size or 0,
            msg.animation.file_name or f"animation_{msg.id}.mp4",
            msg.animation.mime_type or "video/mp4",
        )

    if msg.audio:
        return (
            "audio",
            msg.audio.file_size or 0,
            msg.audio.file_name or f"audio_{msg.id}.mp3",
            msg.audio.mime_type or "audio/mpeg",
        )

    if msg.voice:
        return (
            "voice",
            msg.voice.file_size or 0,
            f"voice_{msg.id}.ogg",
            msg.voice.mime_type or "audio/ogg",
        )

    if msg.video_note:
        return (
            "video_note",
            msg.video_note.file_size or 0,
            f"videonote_{msg.id}.mp4",
            "video/mp4",
        )

    if msg.sticker:
        ext = ".webp"
        if msg.sticker.is_animated:
            ext = ".tgs"
        elif msg.sticker.is_video:
            ext = ".webm"
        return (
            "sticker",
            msg.sticker.file_size or 0,
            f"sticker_{msg.id}{ext}",
            "",
        )

    if msg.document:
        return (
            "document",
            msg.document.file_size or 0,
            msg.document.file_name or f"file_{msg.id}",
            msg.document.mime_type or "",
        )

    if msg.contact:
        return ("contact", 0, "", "")
    if msg.location:
        return ("location", 0, "", "")
    if msg.poll:
        return ("poll", 0, "", "")
    if msg.venue:
        return ("venue", 0, "", "")

    return ("", 0, "", "")


# ═══════════════════════════════════════════════════════════════════════════
# Core: download media
# ═══════════════════════════════════════════════════════════════════════════

async def download_media(
    client: Client,
    message: Message,
    *,
    dest_dir: str = "",
    size_limit: int = 0,
) -> MediaResult:
    """
    Download media from a Pyrogram message.

    Returns MediaResult with file_path set on success.
    """
    dest_dir = dest_dir or DOWNLOAD_DIR
    _ensure_dir(dest_dir)

    result = MediaResult()
    media_type, approx_size, fname, mime = _get_media_meta(message)
    result.media_type = media_type
    result.file_size = approx_size
    result.file_name = fname
    result.mime_type = mime

    # Non-downloadable types
    if not media_type or media_type in ("contact", "location", "poll", "venue"):
        result.method = "none"
        return result

    # Public link
    chat_username = message.chat.username or ""
    result.public_link = _build_link(chat_username, message.id)

    # Check size limit
    effective_limit = size_limit or MAX_DOWNLOAD_SIZE
    if effective_limit and approx_size > effective_limit:
        logger.info(
            "Skipping download: %s (%s) exceeds limit %s",
            fname, _fmt(approx_size), _fmt(effective_limit),
        )
        result.method = "link" if result.public_link else "none"
        return result

    # Download
    t0 = time.monotonic()
    try:
        dest = os.path.join(dest_dir, fname) if fname else dest_dir
        path = await message.download(file_name=dest)
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
        result.mime_type = mimetypes.guess_type(path)[0] or mime
        result.file_name = os.path.basename(path)
        logger.info(
            "Downloaded %s → %s (%s) in %dms",
            media_type, path, _fmt(result.file_size), elapsed,
        )
    else:
        result.error = "download returned None"
        result.method = "link" if result.public_link else "none"
        return result

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
# Core: download + send to log chat
# ═══════════════════════════════════════════════════════════════════════════

async def download_and_attach(
    client: Client,
    message: Message,
    parsed_html: str,
    log_chat: int | str,
    *,
    attach_mode: str = "",
) -> MediaResult:
    """
    Download media from *message*, then send it to *log_chat* alongside
    the parsed HTML summary.

    attach_mode: "file" | "link" | "auto" | "both"
    """
    mode = (attach_mode or ATTACH_MODE).lower()
    result = await download_media(client, message)

    # ── nothing to attach ──
    if result.method == "none":
        await client.send_message(
            log_chat, parsed_html, disable_web_page_preview=True,
        )
        return result

    # ── build link line ──
    from html import escape as esc
    link_line = ""
    if result.public_link:
        link_line = f'\n\n<a href="{esc(result.public_link)}">🔗 Original</a>'

    # Captions are limited to 1024 chars for media messages
    caption = parsed_html
    if len(caption) > 900:
        caption = caption[:900] + "…"

    send_file = False
    send_link_only = False

    if mode == "file":
        send_file = bool(result.file_path)
    elif mode == "link":
        send_link_only = True
    elif mode == "both":
        send_file = bool(result.file_path)
        caption += link_line
    else:  # auto
        send_file = bool(result.file_path)
        if not result.file_path:
            send_link_only = True

    try:
        if send_file and result.file_path:
            # Choose the right send method for the media type
            send_fn = _pick_send_function(client, result.media_type)
            final_caption = caption + (link_line if mode == "both" else "")

            await send_fn(
                log_chat,
                result.file_path,
                caption=final_caption,
            )
            result.method = "both" if (mode == "both" and result.public_link) else "file"

        elif send_link_only or not result.file_path:
            text = parsed_html
            if result.public_link:
                text += link_line
            if result.file_size and not result.file_path:
                text += (
                    f"\n\n📎 <b>Media not downloaded</b> "
                    f"({esc(result.media_type)}, {_fmt(result.file_size)})"
                )
            await client.send_message(
                log_chat, text, disable_web_page_preview=True,
            )
            result.method = "link" if result.public_link else "none"
        else:
            await client.send_message(
                log_chat, parsed_html, disable_web_page_preview=True,
            )

    except Exception as exc:
        logger.error("Failed to send media to log chat: %s", exc)
        result.error = str(exc)
        try:
            await client.send_message(
                log_chat, parsed_html, disable_web_page_preview=True,
            )
        except Exception:
            pass

    # Cleanup
    if result.file_path and not KEEP_FILES:
        try:
            os.remove(result.file_path)
        except OSError:
            pass

    return result


def _pick_send_function(client: Client, media_type: str):
    """Return the appropriate Pyrogram send method for the media type."""
    mapping = {
        "photo":      client.send_photo,
        "video":      client.send_video,
        "gif":        client.send_animation,
        "audio":      client.send_audio,
        "voice":      client.send_voice,
        "video_note": client.send_video_note,
        "sticker":    client.send_sticker,
    }
    return mapping.get(media_type, client.send_document)
