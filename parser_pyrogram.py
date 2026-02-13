"""
Pyrogram Userbot — Channel Parser
====================================
Monitors Telegram channels / groups / private chats using a **regular user
account** (not a bot).  No admin rights required.

Extracted data
--------------
  • Plain text / caption
  • URLs & hyperlinks          • Hashtags & mentions
  • Emails & phone numbers     • Bold / italic / underline / strike / code / spoiler
  • Media metadata             • Forward & reply info
  • Sender identity            • Views & reactions

Requirements:
    pip install pyrogram tgcrypto python-dotenv

First-run auth:
    The script will ask for your phone number and the code Telegram sends.
    A session file is saved and re-used automatically.

Setup:
    1. Go to https://my.telegram.org  → API Development Tools → create an app.
    2. Copy  .env.example → .env  and fill in values.
    3. python parser_pyrogram.py
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Optional

from pyrogram import Client, filters
from pyrogram.enums import ChatType, MessageEntityType, MessageMediaType
from pyrogram.types import Message

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ═══════════════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════════════
API_ID: int       = int(os.getenv("API_ID", "0"))
API_HASH: str     = os.getenv("API_HASH", "")
SESSION_NAME: str = os.getenv("SESSION_NAME", "userbot_parser")
PHONE: str        = os.getenv("PHONE", "")

# Where to send parsed summaries (username or numeric chat id).
LOG_CHAT: str     = os.getenv("LOG_CHAT", "")

# Comma-separated channel/group usernames or numeric IDs to monitor.
# Empty = ALL chats.
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
            WATCH_CHATS.append(item)

JSON_LOG_PATH: str = os.getenv("JSON_LOG_PATH", "")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s — %(message)s",
)
logger = logging.getLogger("pyrogram_parser")


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
    chat_type: str = ""
    chat_title: str = ""
    chat_username: str = ""
    chat_id: int = 0
    message_id: int = 0
    date: Optional[str] = None

    sender_name: str = ""
    sender_username: str = ""
    sender_id: Optional[int] = None

    raw_text: str = ""
    text_length: int = 0

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

    media_type: str = ""
    media_file_name: str = ""
    media_file_size: int = 0
    media_duration: int = 0
    media_mime: str = ""

    forwarded_from: str = ""
    forward_date: Optional[str] = None
    reply_to_message_id: Optional[int] = None
    media_group_id: Optional[str] = None

    reactions_summary: str = ""
    views: Optional[int] = None
    forwards_count: Optional[int] = None

    def to_html(self) -> str:
        sec: list[str] = []
        sec.append(f"<b>📨  Parsed  [{_esc(self.chat_type)}]</b>\n")

        title = self.chat_title or "DM"
        if self.chat_username:
            title += f"  (@{_esc(self.chat_username)})"
        sec.append(f"<b>📡 Chat:</b>  {_esc(title)}")
        sec.append(f"<b>🆔 ID:</b>  <code>{self.chat_id}</code> / msg <code>{self.message_id}</code>")
        if self.date:
            sec.append(f"<b>🕐 Date:</b>  {_esc(self.date)}")
        if self.sender_name:
            sn = self.sender_name
            if self.sender_username:
                sn += f"  (@{_esc(self.sender_username)})"
            sec.append(f"<b>👤 From:</b>  {_esc(sn)}")
        if self.forwarded_from:
            sec.append(f"<b>↩️ Fwd:</b>  {_esc(self.forwarded_from)}")
        if self.reply_to_message_id:
            sec.append(f"<b>💬 Reply:</b>  #{self.reply_to_message_id}")

        stats = []
        if self.views is not None:
            stats.append(f"👁 {self.views}")
        if self.forwards_count is not None:
            stats.append(f"🔁 {self.forwards_count}")
        if stats:
            sec.append(f"<b>📊 Stats:</b>  {'  |  '.join(stats)}")

        # text
        sec.append("")
        sec.append("<b>📝 Text:</b>")
        if self.raw_text:
            preview = self.raw_text[:500] + ("…" if len(self.raw_text) > 500 else "")
            sec.append(f"<pre>{_esc(preview)}</pre>")
            sec.append(f"<i>({self.text_length} chars)</i>")
        else:
            sec.append("<i>— no text —</i>")

        sec += _render_list("🔗 URLs",       self.urls,               limit=12)
        sec += _render_tags("#️⃣ Hashtags",   self.hashtags)
        sec += _render_tags("👤 Mentions",    self.mentions)
        sec += _render_list("✉️ Emails",     self.emails)
        sec += _render_list("📞 Phones",     self.phones)
        sec += _render_list("🅱️ Bold",       self.bold_texts,          limit=8, trim=100)
        sec += _render_list("🔤 Italic",     self.italic_texts,        limit=8, trim=100)
        sec += _render_list("⎁ Underline",   self.underline_texts,     limit=6, trim=100)
        sec += _render_list("🪧 Strike",     self.strikethrough_texts,  limit=6, trim=100)
        sec += _render_code("💻 Code",       self.code_fragments,       limit=5)
        sec += _render_list("🫣 Spoiler",    self.spoiler_texts,        limit=5, trim=80)

        if self.media_type:
            sec.append("")
            parts = [f"<b>📦 Media:</b>  {_esc(self.media_type)}"]
            if self.media_file_name:
                parts.append(f"  name: {_esc(self.media_file_name)}")
            if self.media_mime:
                parts.append(f"  mime: {_esc(self.media_mime)}")
            if self.media_file_size:
                parts.append(f"  size: {_fmt_size(self.media_file_size)}")
            if self.media_duration:
                parts.append(f"  duration: {self.media_duration}s")
            sec.append("\n".join(parts))

        if self.media_group_id:
            sec.append(f"<b>🗂 Album:</b>  <code>{_esc(self.media_group_id)}</code>")

        if self.reactions_summary:
            sec.append(f"<b>❤️ Reactions:</b>  {_esc(self.reactions_summary)}")

        if self.chat_username:
            link = f"https://t.me/{self.chat_username}/{self.message_id}"
            sec.append("")
            sec.append(f'<a href="{link}">🔗 Open original</a>')

        return "\n".join(sec)


def _esc(t: str) -> str:
    return html.escape(str(t))


def _fmt_size(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024  # type: ignore[assignment]
    return f"{b:.1f} TB"


def _render_list(label, items, *, limit=10, trim=0):
    if not items:
        return []
    out = ["", f"<b>{label} ({len(items)}):</b>"]
    for it in items[:limit]:
        t = it[:trim] + "…" if trim and len(it) > trim else it
        out.append(f"  • {_esc(t)}")
    if len(items) > limit:
        out.append(f"  <i>… +{len(items) - limit} more</i>")
    return out


def _render_tags(label, items):
    if not items:
        return []
    return ["", f"<b>{label} ({len(items)}):</b>",
            "  " + "  ".join(_esc(i) for i in items)]


def _render_code(label, items, *, limit=5):
    if not items:
        return []
    out = ["", f"<b>{label} ({len(items)}):</b>"]
    for c in items[:limit]:
        out.append(f"  • <code>{_esc(c[:120])}</code>")
    return out


# ═══════════════════════════════════════════════════════════════════════════
# Entity extraction  (Pyrogram-specific)
# ═══════════════════════════════════════════════════════════════════════════

_PYR_MAP: dict[MessageEntityType, str] = {
    MessageEntityType.URL:           "urls",
    MessageEntityType.TEXT_LINK:     "urls",
    MessageEntityType.HASHTAG:       "hashtags",
    MessageEntityType.MENTION:       "mentions",
    MessageEntityType.TEXT_MENTION:   "mentions",
    MessageEntityType.EMAIL:         "emails",
    MessageEntityType.PHONE_NUMBER:  "phones",
    MessageEntityType.BOLD:          "bold",
    MessageEntityType.ITALIC:        "italic",
    MessageEntityType.UNDERLINE:     "underline",
    MessageEntityType.STRIKETHROUGH: "strikethrough",
    MessageEntityType.CODE:          "code",
    MessageEntityType.PRE:           "code",
    MessageEntityType.SPOILER:       "spoiler",
}

_ALL_BUCKETS = (
    "urls", "hashtags", "mentions", "emails", "phones",
    "bold", "italic", "underline", "strikethrough", "code", "spoiler",
)


def _extract_entities_pyrogram(text: str, entities: list | None) -> dict[str, list[str]]:
    buckets: dict[str, list[str]] = {k: [] for k in _ALL_BUCKETS}
    if not entities or not text:
        return buckets

    for ent in entities:
        bucket = _PYR_MAP.get(ent.type)
        if bucket is None:
            continue

        # Pyrogram uses normal Python str offsets (UTF-8-aware)
        fragment = text[ent.offset : ent.offset + ent.length]

        if ent.type == MessageEntityType.TEXT_LINK:
            buckets["urls"].append(ent.url or fragment)
        elif ent.type == MessageEntityType.TEXT_MENTION and ent.user:
            name = f"{ent.user.first_name or ''} {ent.user.last_name or ''}".strip()
            buckets["mentions"].append(name or f"id:{ent.user.id}")
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


def _detect_media_pyrogram(msg: Message) -> tuple[str, str, int, int, str]:
    if not msg.media:
        return ("", "", 0, 0, "")

    mtype = ""
    fname = ""
    fsize = 0
    dur = 0
    mime = ""

    if msg.photo:
        mtype = "photo"
        fsize = msg.photo.file_size or 0
    elif msg.video:
        mtype = "video"
        fname = msg.video.file_name or ""
        fsize = msg.video.file_size or 0
        dur   = msg.video.duration or 0
        mime  = msg.video.mime_type or ""
    elif msg.animation:
        mtype = "gif"
        fsize = msg.animation.file_size or 0
        dur   = msg.animation.duration or 0
        mime  = msg.animation.mime_type or ""
    elif msg.audio:
        mtype = "audio"
        fname = msg.audio.file_name or ""
        fsize = msg.audio.file_size or 0
        dur   = msg.audio.duration or 0
        mime  = msg.audio.mime_type or ""
    elif msg.voice:
        mtype = "voice"
        fsize = msg.voice.file_size or 0
        dur   = msg.voice.duration or 0
        mime  = msg.voice.mime_type or ""
    elif msg.video_note:
        mtype = "video_note"
        fsize = msg.video_note.file_size or 0
        dur   = msg.video_note.duration or 0
    elif msg.sticker:
        mtype = "sticker"
        fsize = msg.sticker.file_size or 0
    elif msg.document:
        mtype = "document"
        fname = msg.document.file_name or ""
        fsize = msg.document.file_size or 0
        mime  = msg.document.mime_type or ""
    elif msg.contact:
        mtype = "contact"
    elif msg.location:
        mtype = "location"
    elif msg.poll:
        mtype = "poll"
    elif msg.venue:
        mtype = "venue"
    elif msg.web_page:
        mtype = "webpage"

    return (mtype, fname, fsize, dur, mime)


def _get_reactions_pyrogram(msg: Message) -> str:
    reactions = getattr(msg, "reactions", None)
    if not reactions:
        return ""
    reaction_list = getattr(reactions, "reactions", None)
    if not reaction_list:
        return ""
    parts = []
    for r in reaction_list:
        emoji = getattr(r, "emoji", "") or ""
        count = getattr(r, "count", 0)
        if emoji:
            parts.append(f"{emoji}×{count}")
    return "  ".join(parts)


def parse_message_pyrogram(msg: Message) -> ParsedPost:
    text = msg.text or msg.caption or ""
    entities = msg.entities or msg.caption_entities or []

    ent = _extract_entities_pyrogram(text, entities)
    reg = _extract_regex(text)

    # Chat info
    chat = msg.chat
    chat_title = chat.title or ""
    chat_username = chat.username or ""
    chat_id = chat.id

    type_map = {
        ChatType.CHANNEL:    "channel",
        ChatType.SUPERGROUP: "supergroup",
        ChatType.GROUP:      "group",
        ChatType.PRIVATE:    "private",
    }
    chat_type = type_map.get(chat.type, "unknown")
    if chat_type == "private":
        chat_title = f"{chat.first_name or ''} {chat.last_name or ''}".strip()

    # Sender
    sender_name = ""
    sender_username = ""
    sender_id = None
    if msg.from_user:
        sender_name = f"{msg.from_user.first_name or ''} {msg.from_user.last_name or ''}".strip()
        sender_username = msg.from_user.username or ""
        sender_id = msg.from_user.id
    elif msg.sender_chat:
        sender_name = msg.sender_chat.title or ""
        sender_username = msg.sender_chat.username or ""
        sender_id = msg.sender_chat.id

    # Media
    mtype, mname, msize, mdur, mmime = _detect_media_pyrogram(msg)

    # Forward
    fwd_name = ""
    fwd_date = None
    if msg.forward_from:
        fwd_name = f"{msg.forward_from.first_name or ''} {msg.forward_from.last_name or ''}".strip()
    elif msg.forward_from_chat:
        fwd_name = msg.forward_from_chat.title or ""
    elif msg.forward_sender_name:
        fwd_name = msg.forward_sender_name
    if msg.forward_date:
        fwd_date = msg.forward_date.isoformat()

    # Reactions
    reactions = _get_reactions_pyrogram(msg)

    post = ParsedPost(
        chat_type        = chat_type,
        chat_title       = chat_title,
        chat_username    = chat_username,
        chat_id          = chat_id,
        message_id       = msg.id,
        date             = msg.date.isoformat() if msg.date else None,
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
        forwarded_from   = fwd_name,
        forward_date     = fwd_date,
        reply_to_message_id = (
            msg.reply_to_message_id if msg.reply_to_message_id else None
        ),
        media_group_id   = str(msg.media_group_id) if msg.media_group_id else None,
        reactions_summary = reactions,
        views            = msg.views,
        forwards_count   = msg.forwards,
    )

    return post


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
# Client & handler
# ═══════════════════════════════════════════════════════════════════════════

app = Client(
    SESSION_NAME,
    api_id=API_ID,
    api_hash=API_HASH,
    phone_number=PHONE or None,
)

# Build the chat filter
_chat_filter = filters.chat(WATCH_CHATS) if WATCH_CHATS else filters.all
_combined_filter = _chat_filter & ~filters.service


@app.on_message(_combined_filter)
async def on_new_message(client_: Client, message: Message) -> None:
    """Fires on every new message in monitored chats."""

    logger.info(
        "📩  msg #%s  in chat %s  media=%s",
        message.id,
        message.chat.id,
        message.media or "text",
    )

    try:
        parsed = parse_message_pyrogram(message)
    except Exception as exc:
        logger.error("Parse failed for msg #%s: %s", message.id, exc)
        return

    save_json(parsed)

    logger.info(
        "    ├─ [%s] «%s»  text=%d  urls=%d  tags=%d",
        parsed.chat_type,
        parsed.chat_title[:30],
        parsed.text_length,
        len(parsed.urls),
        len(parsed.hashtags),
    )
    logger.info(
        "    └─ media=%s  views=%s  reactions=%s",
        parsed.media_type or "—",
        parsed.views if parsed.views is not None else "—",
        parsed.reactions_summary or "—",
    )

    if LOG_CHAT:
        try:
            target: int | str
            try:
                target = int(LOG_CHAT)
            except ValueError:
                target = LOG_CHAT
            await client_.send_message(
                target,
                parsed.to_html(),
                disable_web_page_preview=True,
            )
        except Exception as exc:
            logger.error("Send to log chat failed: %s", exc)
    else:
        print(parsed.to_html())


# ═══════════════════════════════════════════════════════════════════════════
# Bootstrap
# ═══════════════════════════════════════════════════════════════════════════

def main() -> None:
    if not API_ID or not API_HASH:
        logger.error("❌  Set API_ID and API_HASH (from https://my.telegram.org)")
        return

    logger.info(
        "🚀  Starting Pyrogram userbot parser…\n"
        "    SESSION   = %s\n"
        "    LOG_CHAT  = %s\n"
        "    WATCH     = %s\n"
        "    JSON_LOG  = %s",
        SESSION_NAME,
        LOG_CHAT or "(console)",
        WATCH_CHATS or "(all chats)",
        JSON_LOG_PATH or "(off)",
    )

    app.run()


if __name__ == "__main__":
    main()
