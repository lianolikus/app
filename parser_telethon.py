"""
Telethon Userbot — Channel Parser
===================================
Monitors Telegram channels / groups / private chats using a **regular user
account** (not a bot).  No admin rights, no privacy-mode tricks — just a
normal member that can read messages.

Extracted data
--------------
  • Plain text / caption
  • URLs & hyperlinks          • Hashtags & mentions
  • Emails & phone numbers     • Bold / italic / underline / strike / code / spoiler
  • Media metadata             • Forward & reply info
  • Sender identity            • Reactions (if available)

Requirements:
    pip install telethon python-dotenv

First-run auth:
    The script will ask for your phone number and the code Telegram sends.
    After that a session file is saved and re-used automatically.

Setup:
    1. Go to https://my.telegram.org  → API Development Tools → create an app.
       Copy API_ID and API_HASH.
    2. Copy  .env.example → .env  and fill in values.
    3. python parser_telethon.py
       (first run: enter phone + code; afterwards it starts silently)
"""

from __future__ import annotations

from tz_helper import format_dt, format_iso
import asyncio
import html
import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Optional
from media_downloader_telethon import download_and_attach, MediaResult
from telethon import TelegramClient, events
from telethon.tl.types import (
    Channel,
    Chat,
    MessageEntityBold,
    MessageEntityCode,
    MessageEntityEmail,
    MessageEntityHashtag,
    MessageEntityItalic,
    MessageEntityMention,
    MessageEntityMentionName,
    MessageEntityPhone,
    MessageEntityPre,
    MessageEntitySpoiler,
    MessageEntityStrike,
    MessageEntityTextUrl,
    MessageEntityUnderline,
    MessageEntityUrl,
    MessageMediaContact,
    MessageMediaDocument,
    MessageMediaGeo,
    MessageMediaGeoLive,
    MessageMediaPhoto,
    MessageMediaPoll,
    MessageMediaVenue,
    MessageMediaWebPage,
    PeerChannel,
    PeerChat,
    PeerUser,
    User,
)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ═══════════════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════════════
API_ID: int         = int(os.getenv("API_ID", "0"))
API_HASH: str       = os.getenv("API_HASH", "")
SESSION_NAME: str   = os.getenv("SESSION_NAME", "userbot_parser")
PHONE: str          = os.getenv("PHONE", "")          # e.g. +380991234567

# Where to send parsed summaries (username, phone, or numeric chat id).
# Leave empty to print to console only.
LOG_CHAT: str       = os.getenv("LOG_CHAT", "")

# Comma-separated channel/group usernames or IDs to monitor.
# Empty = monitor ALL incoming messages (every chat the account is in).
WATCH_CHATS: list[str | int] = []
_raw = os.getenv("WATCH_CHATS", "")
if _raw.strip():
    for item in _raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            WATCH_CHATS.append(int(item))
        except ValueError:
            WATCH_CHATS.append(item)       # username like "durov"

# Optional JSON-lines log file
JSON_LOG_PATH: str = os.getenv("JSON_LOG_PATH", "")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s — %(message)s",
)
logger = logging.getLogger("telethon_parser")


# ═══════════════════════════════════════════════════════════════════════════
# Regex fallbacks
# ═══════════════════════════════════════════════════════════════════════════
RE_URL     = re.compile(r"https?://[^\s<>\"']+", re.I)
RE_EMAIL   = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
RE_PHONE   = re.compile(r"\+?[(]?[0-9]{1,4}[)]?[-\s./0-9]{6,15}")
RE_HASHTAG = re.compile(r"#[A-Za-zА-Яа-яёЁІіЇїЄєҐґ0-9_]+")
RE_MENTION = re.compile(r"@[A-Za-z0-9_]{3,}")


# ═══════════════════════════════════════════════════════════════════════════
# Data model
# ═══════════════════════════════════════════════════════════════════════════
@dataclass
class ParsedPost:
    """All information extracted from one message."""

    # ── source ──
    chat_type: str = ""                  # "channel" | "group" | "supergroup" | "private"
    chat_title: str = ""
    chat_username: str = ""
    chat_id: int = 0
    message_id: int = 0
    date: Optional[str] = None           # ISO-8601

    # ── sender ──
    sender_name: str = ""
    sender_username: str = ""
    sender_id: Optional[int] = None

    # ── text ──
    raw_text: str = ""
    text_length: int = 0

    # ── entities ──
    urls: list[str]                = field(default_factory=list)
    hashtags: list[str]            = field(default_factory=list)
    mentions: list[str]            = field(default_factory=list)
    emails: list[str]              = field(default_factory=list)
    phones: list[str]              = field(default_factory=list)
    bold_texts: list[str]          = field(default_factory=list)
    italic_texts: list[str]        = field(default_factory=list)
    underline_texts: list[str]     = field(default_factory=list)
    strikethrough_texts: list[str] = field(default_factory=list)
    code_fragments: list[str]      = field(default_factory=list)
    spoiler_texts: list[str]       = field(default_factory=list)

    # ── media ──
    media_type: str = ""                 # "photo" | "video" | "document" | …
    media_file_name: str = ""
    media_file_size: int = 0
    media_duration: int = 0
    media_mime: str = ""
    has_webpage_preview: bool = False
    webpage_url: str = ""

    # ── forward / reply / grouping ──
    forwarded_from: str = ""
    forward_date: Optional[str] = None
    reply_to_message_id: Optional[int] = None
    grouped_id: Optional[int] = None     # album

    # ── reactions (Telethon ≥ 1.28) ──
    reactions_summary: str = ""

    # ── views / shares ──
    views: Optional[int] = None
    forwards: Optional[int] = None

    downloaded_path: str = ""
    download_method: str = ""            # file | link | both | none
    public_link: str = ""

    # ──────────────────────────────────────────────────────────────────────
    def to_html(self) -> str:
        sec: list[str] = []
        sec.append(f"<b>📨 New Parsed Message </b>\n")

        sec.append("<b>📝 Text: </b>")
        if self.raw_text:
            preview = self.raw_text[:1000] + ("…" if len(self.raw_text) > 1000 else "")
            sec.append(f"<pre>{_esc(preview)}</pre>")
        else:
            sec.append("<i>— no text —</i>")

        # source
        title = self.chat_title or "DM"
        sec.append(f"\n<b>📡 {_esc(self.chat_type).capitalize()}: </b> <a href='https://t.me/{self.chat_username}'>{_esc(title)}</a>")
        # sec.append(f"<b>🆔 ID:</b>  <code>{self.chat_id}</code> / msg <code>{self.message_id}</code>")
        if self.date:
            sec.append(f"<b>🕐 Date:</b> {_esc(self.date)[:16]}")

        # # sender
        # if self.sender_name:
        #     sn = self.sender_name
        #     if self.sender_username:
        #         sn += f"  (@{_esc(self.sender_username)})"
        #     sec.append(f"<b>👤 From:</b>  {_esc(sn)}")

        if self.forwarded_from:
            sec.append(f"<b>↩️ Fwd:</b>  {_esc(self.forwarded_from)}")
        if self.reply_to_message_id:
            sec.append(f"<b>💬 Reply to:</b>  #{self.reply_to_message_id}")

        # # views / forwards
        # stats = []
        # if self.views is not None:
        #     stats.append(f"👁 {self.views}")
        # if self.forwards is not None:
        #     stats.append(f"🔁 {self.forwards}")
        # if stats:
        #     sec.append(f"<b>📊 Stats:</b>  {'  |  '.join(stats)}")

        # text
        # sec.append("")


        # entities
        sec += _render_list("🔗 URLs",       self.urls,               limit=12)
        sec += _render_tags("#️⃣ Hashtags",   self.hashtags)
        sec += _render_tags("👤 Mentions",    self.mentions)
        sec += _render_list("✉️ Emails",     self.emails)
        # sec += _render_list("📞 Phones",     self.phones)
        sec += _render_list("🅱️ Bold",       self.bold_texts,          limit=8, trim=100)
        sec += _render_list("🔤 Italic",     self.italic_texts,        limit=8, trim=100)
        sec += _render_list("⎁ Underline",   self.underline_texts,     limit=6, trim=100)
        sec += _render_list("🪧 Strike",     self.strikethrough_texts,  limit=6, trim=100)
        sec += _render_code("💻 Code",       self.code_fragments,       limit=5)
        sec += _render_list("🫣 Spoiler",    self.spoiler_texts,        limit=5, trim=80)


        if self.has_webpage_preview and self.webpage_url:
            sec.append(f"<b>🌐 Preview:</b>  {_esc(self.webpage_url)}")

        if self.downloaded_path:
            sec.append(f"<b>💾 Saved:</b>  <code>{_esc(self.downloaded_path)}</code>")
        if self.download_method:
            sec.append(f"<b>📤 Attach:</b>  {_esc(self.download_method)}")

        if self.grouped_id:
            sec.append(f"<b>🗂 Album:</b>  <code>{self.grouped_id}</code>")

        # if self.reactions_summary:
        #     sec.append(f"<b>❤️ Reactions:</b>  {_esc(self.reactions_summary)}")

        # link to original (public channels)
        if self.chat_username:
            link = f"https://t.me/{self.chat_username}/{self.message_id}"
            sec.append("")
            sec.append(f'<a href="{link}">🔗 Open original</a>')

        return "\n".join(sec)


# ── helpers ───────────────────────────────────────────────────────────────

def _esc(t: str) -> str:
    return html.escape(str(t))


def _fmt_size(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024  # type: ignore[assignment]
    return f"{b:.1f} TB"


def _render_list(label: str, items: list[str], *, limit: int = 10, trim: int = 0) -> list[str]:
    if not items:
        return []
    out = ["", f"<b>{label} ({len(items)}):</b>"]
    for it in items[:limit]:
        t = it[:trim] + "…" if trim and len(it) > trim else it
        out.append(f"  • {_esc(t)}")
    if len(items) > limit:
        out.append(f"  <i>… +{len(items) - limit} more</i>")
    return out


def _render_tags(label: str, items: list[str]) -> list[str]:
    if not items:
        return []
    return ["", f"<b>{label} ({len(items)}):</b>",
            "  " + "  ".join(_esc(i) for i in items)]


def _render_code(label: str, items: list[str], *, limit: int = 5) -> list[str]:
    if not items:
        return []
    out = ["", f"<b>{label} ({len(items)}):</b>"]
    for c in items[:limit]:
        out.append(f"  • <code>{_esc(c[:120])}</code>")
    return out


# ═══════════════════════════════════════════════════════════════════════════
# Extraction engine
# ═══════════════════════════════════════════════════════════════════════════

# Telethon entity type → bucket name
_ENTITY_MAP: dict[type, str] = {
    MessageEntityUrl:         "urls",
    MessageEntityTextUrl:     "urls",
    MessageEntityHashtag:     "hashtags",
    MessageEntityMention:     "mentions",
    MessageEntityMentionName: "mentions",
    MessageEntityEmail:       "emails",
    MessageEntityPhone:       "phones",
    MessageEntityBold:        "bold",
    MessageEntityItalic:      "italic",
    MessageEntityUnderline:   "underline",
    MessageEntityStrike:      "strikethrough",
    MessageEntityCode:        "code",
    MessageEntityPre:         "code",
    MessageEntitySpoiler:     "spoiler",
}

_ALL_BUCKETS = (
    "urls", "hashtags", "mentions", "emails", "phones",
    "bold", "italic", "underline", "strikethrough", "code", "spoiler",
)


def _extract_entities(text: str, entities: list | None) -> dict[str, list[str]]:
    buckets: dict[str, list[str]] = {k: [] for k in _ALL_BUCKETS}
    if not entities or not text:
        return buckets

    for ent in entities:
        bucket = _ENTITY_MAP.get(type(ent))
        if bucket is None:
            continue

        fragment = text[ent.offset : ent.offset + ent.length]

        if isinstance(ent, MessageEntityTextUrl):
            buckets["urls"].append(ent.url or fragment)
        elif isinstance(ent, MessageEntityMentionName):
            buckets["mentions"].append(f"id:{ent.user_id}")
        else:
            buckets[bucket].append(fragment)

    return buckets


def _extract_regex(text: str) -> dict[str, list[str]]:
    return {
        "urls":     RE_URL.findall(text),
        "hashtags": RE_HASHTAG.findall(text),
        "mentions": RE_MENTION.findall(text),
        "emails":   RE_EMAIL.findall(text),
        "phones":   RE_PHONE.findall(text),
    }


def _merge(a: list[str], b: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in a + b:
        key = item.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(item.strip())
    return out


def _detect_media(message) -> tuple[str, str, int, int, str]:
    """Return (type, filename, size, duration, mime)."""
    media = message.media
    if media is None:
        return ("", "", 0, 0, "")
    if isinstance(media, MessageMediaPhoto):
        ph = media.photo
        sz = 0
        if ph and ph.sizes:
            sz = getattr(ph.sizes[-1], "size", 0) or 0
        return ("photo", sz, "", 0, "image/jpeg")

    if isinstance(media, MessageMediaDocument):
        doc = media.document
        if doc is None:
            return ("document", "", 0, 0, "")

        mime = doc.mime_type or ""
        size = doc.size or 0
        fname = ""
        duration = 0

        for attr in doc.attributes:
            cls_name = type(attr).__name__
            if hasattr(attr, "file_name") and attr.file_name:
                fname = attr.file_name
            if hasattr(attr, "duration"):
                duration = attr.duration or 0

        # Classify by mime / attributes
        if "video" in mime and not getattr(media, "round", False):
            if any(type(a).__name__ == "DocumentAttributeAnimated" for a in doc.attributes):
                mtype = "gif"
            elif any(type(a).__name__ == "DocumentAttributeVideo" for a in doc.attributes):
                round_msg = any(
                    getattr(a, "round_message", False)
                    for a in doc.attributes
                    if type(a).__name__ == "DocumentAttributeVideo"
                )
                mtype = "video_note" if round_msg else "video"
            else:
                mtype = "video"
        elif "audio" in mime:
            if any(type(a).__name__ == "DocumentAttributeAudio" for a in doc.attributes):
                is_voice = any(
                    getattr(a, "voice", False)
                    for a in doc.attributes
                    if type(a).__name__ == "DocumentAttributeAudio"
                )
                mtype = "voice" if is_voice else "audio"
            else:
                mtype = "audio"
        elif any(type(a).__name__ == "DocumentAttributeSticker" for a in doc.attributes):
            mtype = "sticker"
        else:
            mtype = "document"

        return (mtype, fname, size, duration, mime)

    if isinstance(media, MessageMediaContact):
        return ("contact", "", 0, 0, "")
    if isinstance(media, (MessageMediaGeo, MessageMediaGeoLive)):
        return ("location", "", 0, 0, "")
    if isinstance(media, MessageMediaPoll):
        return ("poll", "", 0, 0, "")
    if isinstance(media, MessageMediaVenue):
        return ("venue", "", 0, 0, "")
    if isinstance(media, MessageMediaWebPage):
        url = ""
        if media.webpage and hasattr(media.webpage, "url"):
            url = media.webpage.url or ""
        return ("webpage", "", 0, 0, url)      # url stored in mime slot temporarily

    return ("other", "", 0, 0, "")


async def _resolve_forward(message) -> tuple[str, str | None]:
    """Return (forward_from_name, forward_date_iso)."""
    fwd = message.forward
    if fwd is None:
        return ("", None)

    name = ""
    if fwd.sender_id:
        try:
            entity = await message.client.get_entity(fwd.sender_id)
            if isinstance(entity, User):
                name = f"{entity.first_name or ''} {entity.last_name or ''}".strip()
            elif isinstance(entity, Channel):
                name = entity.title or ""
        except Exception:
            name = f"id:{fwd.sender_id}"
    elif fwd.from_name:
        name = fwd.from_name

    fwd_date = format_dt(fwd.date) if fwd.date else None
    return (name, fwd_date)


def _get_reactions(message) -> str:
    """Summarise reactions as a compact string."""
    results = getattr(message, "reactions", None)
    if not results:
        return ""
    reaction_list = getattr(results, "results", None)
    if not reaction_list:
        return ""

    parts: list[str] = []
    for r in reaction_list:
        emoticon = ""
        reaction = getattr(r, "reaction", None)
        if reaction:
            emoticon = getattr(reaction, "emoticon", "") or ""
        count = getattr(r, "count", 0)
        if emoticon:
            parts.append(f"{emoticon}×{count}")
    return "  ".join(parts)


async def parse_message(message) -> ParsedPost:
    """Telethon Message → ParsedPost."""

    text = message.text or ""
    entities = message.entities or []

    ent = _extract_entities(text, entities)
    reg = _extract_regex(text)

    # Chat info
    chat = await message.get_chat()
    chat_title = getattr(chat, "title", "") or ""
    chat_username = getattr(chat, "username", "") or ""
    chat_id = message.chat_id or 0

    if isinstance(chat, Channel):
        chat_type = "channel" if chat.broadcast else "supergroup"
    elif isinstance(chat, Chat):
        chat_type = "group"
    elif isinstance(chat, User):
        chat_type = "private"
        chat_title = f"{chat.first_name or ''} {chat.last_name or ''}".strip()
    else:
        chat_type = "unknown"

    # Sender
    sender = await message.get_sender()
    sender_name = ""
    sender_username = ""
    sender_id = None
    if sender:
        if isinstance(sender, User):
            sender_name = f"{sender.first_name or ''} {sender.last_name or ''}".strip()
            sender_username = sender.username or ""
            sender_id = sender.id
        elif isinstance(sender, Channel):
            sender_name = sender.title or ""
            sender_username = sender.username or ""
            sender_id = sender.id

    # Media
    mtype, mname, msize, mdur, mmime = _detect_media(message)
    has_webpage = False
    webpage_url = ""
    if mtype == "webpage":
        has_webpage = True
        webpage_url = mmime  # temporarily stored there
        mtype = ""
        mmime = ""

    # Forward
    fn, fd = await _resolve_forward(message)

    # # # Reactions
    # reactions = _get_reactions(message)

    
    return ParsedPost(
        chat_type        = chat_type,
        chat_title       = chat_title,
        chat_username    = chat_username,
        chat_id          = chat_id,
        message_id       = message.id,
        date             = format_dt(message.date) if message.date else None,
        sender_name      = sender_name,
        sender_username  = sender_username,
        sender_id        = sender_id,
        raw_text         = text,
        text_length      = len(text),
        urls             = _merge(ent["urls"],     reg["urls"]),
        hashtags         = _merge(ent["hashtags"], reg["hashtags"]),
        mentions         = _merge(ent["mentions"], reg["mentions"]),
        emails           = _merge(ent["emails"],   reg["emails"]),
        phones           = _merge(ent["phones"],   reg["phones"]),
        bold_texts       = ent["bold"],
        italic_texts     = ent["italic"],
        underline_texts  = ent["underline"],
        strikethrough_texts = ent["strikethrough"],
        code_fragments   = ent["code"],
        spoiler_texts    = ent["spoiler"],
        media_type       = mtype,
        media_file_name  = mname,
        media_file_size  = msize,
        media_duration   = mdur,
        media_mime       = mmime,
        has_webpage_preview = has_webpage,
        webpage_url      = webpage_url,
        forwarded_from   = fn,
        forward_date     = fd,
        reply_to_message_id = 
            message.reply_to.reply_to_msg_id
            if message.reply_to else None,
        grouped_id       = message.grouped_id,
        reactions_summary = _get_reactions(message),
        views            = message.views,
        forwards         = message.forwards,
    )


# ═══════════════════════════════════════════════════════════════════════════
# JSON logger
# ═══════════════════════════════════════════════════════════════════════════

def save_json(post: ParsedPost) -> None:
    if not JSON_LOG_PATH:
        return
    try:
        with open(JSON_LOG_PATH, "a", encoding="utf-8") as fh:
            json.dump(asdict(post), fh, ensure_ascii=False)
            fh.write("\n")
    except OSError as exc:
        logger.error("JSON write failed: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════
# Client & event handler
# ═══════════════════════════════════════════════════════════════════════════

client = TelegramClient(SESSION_NAME, API_ID, API_HASH)


@client.on(events.NewMessage(chats=WATCH_CHATS or None))
async def on_new_message(event: events.NewMessage.Event) -> None:
    """Fires on every new message in monitored chats."""

    message = event.message

    logger.info(
        "📩  msg #%s  in chat %s  type=%s",
        message.id,
        message.chat_id,
        type(message.media).__name__ if message.media else "text",
    )

    try:
        parsed = await parse_message(message)
    except Exception as exc:
        logger.error("Parse failed for msg #%s: %s", message.id, exc)
        return

    logger.info(
        "    ├─ [%s] «%s»  text=%d  urls=%d  tags=%d",
        parsed.chat_type,
        parsed.chat_title[:30],
        parsed.text_length,
        len(parsed.urls),
        len(parsed.hashtags),
    )

    # Send summary to log chat
    if LOG_CHAT:
        target: int | str
        try:
            target = int(LOG_CHAT)
        except ValueError:
            target = LOG_CHAT

        if message.media:
            result: MediaResult = await download_and_attach(
                client, message, parsed.to_html(), target,
            )
            # Update parsed post with download info
            parsed.downloaded_path = result.file_path or ""
            parsed.download_method = result.method
            parsed.public_link = result.public_link
            logger.info(
                "    └─ media: %s  method=%s  path=%s",
                result.media_type, result.method, result.file_path or "—",
            )
        else:
            # No media — just send the text summary
            try:
                await client.send_message(
                    target, parsed.to_html(),
                    parse_mode="html", link_preview=False,
                )
            except Exception as exc:
                logger.error("Send failed: %s", exc)
    else:
        print(parsed.to_html())

    save_json(parsed)
# ═══════════════════════════════════════════════════════════════════════════
# Bootstrap
# ═══════════════════════════════════════════════════════════════════════════

async def main() -> None:
    if not API_ID or not API_HASH:
        logger.error("❌  Set API_ID and API_HASH (from https://my.telegram.org)")
        return

    logger.info(
        "🚀  Starting Telethon userbot parser…\n"
        "    SESSION   = %s\n"
        "    LOG_CHAT  = %s\n"
        "    WATCH     = %s\n"
        "    JSON_LOG  = %s",
        SESSION_NAME,
        LOG_CHAT or "(console)",
        WATCH_CHATS or "(all chats)",
        JSON_LOG_PATH or "(off)",
    )

    await client.start(phone=PHONE or None)
    me = await client.get_me()
    logger.info("✅  Logged in as %s (id=%s)", me.first_name, me.id)

    logger.info("👂  Listening for new messages…  Press Ctrl+C to stop.")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
