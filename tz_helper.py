"""
Timezone Helper
================
Converts UTC timestamps from Telegram API to the configured local timezone.

Usage:
    from tz_helper import format_dt, to_local

    # Convert a UTC datetime to local and format as string
    local_str = format_dt(message.date)           # "2026-02-11 15:30:45 EET"

    # Just convert without formatting
    local_dt = to_local(message.date)

Configuration (in .env):
    TIMEZONE=Europe/Kyiv          ← IANA timezone name
    DATE_FORMAT=%Y-%m-%d %H:%M:%S %Z   ← strftime format

If TIMEZONE is not set, defaults to UTC.

Common timezone values:
    Europe/Kyiv, Europe/Moscow, Europe/London, Europe/Berlin,
    US/Eastern, US/Pacific, Asia/Tokyo, Asia/Shanghai, etc.

Requirements:
    Python 3.9+  (zoneinfo is built-in)
    pip install tzdata          ← needed only on Windows
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, available_timezones

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ═══════════════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════════════

_tz_name: str = os.getenv("TIMEZONE", "UTC")
_date_fmt: str = os.getenv("DATE_FORMAT", "%Y-%m-%d %H:%M:%S %Z")

# Validate the timezone name
if _tz_name not in available_timezones() and _tz_name != "UTC":
    import logging
    logging.getLogger("tz_helper").warning(
        "Unknown timezone '%s', falling back to UTC. "
        "Use IANA names like 'Europe/Kyiv', 'US/Eastern', etc.",
        _tz_name,
    )
    _tz_name = "UTC"

LOCAL_TZ: ZoneInfo = ZoneInfo(_tz_name)
UTC: timezone = timezone.utc


# ═══════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════

def to_local(dt: datetime | None) -> datetime | None:
    """
    Convert a datetime to the configured local timezone.

    Handles three cases:
      1. Aware datetime (has tzinfo)  → convert directly
      2. Naive datetime               → assume UTC first, then convert
      3. None                         → return None

    Telethon:  message.date is always UTC-aware (datetime with tzinfo=UTC)
    Pyrogram:  message.date is a naive datetime but represents UTC
    """
    if dt is None:
        return None

    # Case 1: aware datetime — convert directly
    if dt.tzinfo is not None:
        return dt.astimezone(LOCAL_TZ)

    # Case 2: naive datetime — treat as UTC, then convert
    return dt.replace(tzinfo=UTC).astimezone(LOCAL_TZ)


def format_dt(
    dt: datetime | None,
    fmt: str = "",
    *,
    include_utc: bool = False,
) -> str:
    """
    Convert to local timezone and format as string.

    Parameters
    ----------
    dt : datetime or None
    fmt : strftime format (defaults to DATE_FORMAT from .env)
    include_utc : if True, append the original UTC time in parentheses

    Returns
    -------
    Formatted string like "2026-02-11 15:30:45 EET"
    or "(no date)" if dt is None.

    Examples
    --------
    >>> format_dt(msg.date)
    '2026-02-11 17:30:45 EET'

    >>> format_dt(msg.date, include_utc=True)
    '2026-02-11 17:30:45 EET  (15:30 UTC)'
    """
    if dt is None:
        return "(no date)"

    fmt = fmt or _date_fmt
    local = to_local(dt)
    result = local.strftime(fmt)

    if include_utc:
        utc_dt = dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        utc_str = utc_dt.astimezone(UTC).strftime("%H:%M UTC")
        result += f"  ({utc_str})"

    return result


def format_iso(dt: datetime | None) -> str:
    """Convert to local timezone and return ISO-8601 string."""
    if dt is None:
        return ""
    local = to_local(dt)
    return local.isoformat()


def get_timezone_info() -> dict:
    """Return current timezone configuration (useful for /status)."""
    from datetime import datetime as _dt
    now_local = _dt.now(LOCAL_TZ)
    return {
        "timezone_name": _tz_name,
        "utc_offset": now_local.strftime("%z"),        # e.g. "+0200"
        "abbreviation": now_local.strftime("%Z"),      # e.g. "EET"
        "date_format": _date_fmt,
        "current_local_time": now_local.strftime(_date_fmt),
    }
