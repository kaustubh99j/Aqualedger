"""Small shared helpers: formatting, JSON serialisation, validation."""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta, timezone

from flask import jsonify

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
HOUSEHOLD_CODE_RE = re.compile(r"^[A-Z0-9\-]{3,32}$")
WALLET_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


class ValidationError(ValueError):
    """Raised for expected, user-fixable input errors."""


def json_default(value):
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "quantize"):
        return float(value)
    if isinstance(value, bytes):
        return "0x" + value.hex()
    return str(value)


def api_error(message: str, status: int = 400, **extra):
    payload = {"ok": False, "error": message}
    payload.update(extra)
    return jsonify(payload), status


def api_ok(**payload):
    return jsonify({"ok": True, **payload})


def parse_date(value: str | None, field: str = "date") -> date:
    if not value:
        raise ValidationError(f"{field} is required")
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValidationError(f"{field} must be in YYYY-MM-DD format") from exc


def parse_float(value, field: str, minimum: float | None = None, maximum: float | None = None) -> float:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field} must be a number") from exc
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        raise ValidationError(f"{field} must be a finite number")
    if minimum is not None and parsed < minimum:
        raise ValidationError(f"{field} must be at least {minimum:g}")
    if maximum is not None and parsed > maximum:
        raise ValidationError(f"{field} must be at most {maximum:g}")
    return round(parsed, 3)


def month_bounds(day: date) -> tuple[date, date]:
    last = calendar.monthrange(day.year, day.month)[1]
    return day.replace(day=1), day.replace(day=last)


def add_months(day: date, delta: int) -> date:
    month = day.month - 1 + delta
    year = day.year + month // 12
    month = month % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last_day))


def fmt_l(value, digits: int = 0) -> str:
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return "0"


def short_hash(value, head: int = 8, tail: int = 6) -> str:
    """Abbreviate a 0x-prefixed hash for display, e.g. 0x1234abcd…9f0e12."""
    if not value:
        return "--"
    text = str(value)
    if len(text) <= head + tail + 2:
        return text
    return f"{text[:head]}…{text[-tail:]}"


def fmt_money(value, digits: int = 2) -> str:
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return "0.00"


def pct(part, whole, digits: int = 0) -> float:
    try:
        whole = float(whole)
        if whole <= 0:
            return 0.0
        return round(float(part) / whole * 100, digits)
    except (TypeError, ValueError):
        return 0.0


def humanize_dt(value) -> str:
    if not value:
        return "--"
    if isinstance(value, str):
        return value
    return value.strftime("%d %b %Y, %H:%M UTC")


def time_ago(value) -> str:
    if not value:
        return "--"
    if isinstance(value, str):
        try:
            value = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return value
    if value.tzinfo:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    delta = datetime.now(timezone.utc).replace(tzinfo=None) - value
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} h ago"
    if seconds < 604800:
        return f"{seconds // 86400} d ago"
    return value.strftime("%d %b %Y")


def last_n_days(n: int, end: date | None = None) -> list[date]:
    end = end or date.today()
    return [end - timedelta(days=offset) for offset in range(n - 1, -1, -1)]


def parse_iso_ts(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None
