"""Dates. Stored as ISO ``YYYY-MM-DD``, typed as something shorter."""

from __future__ import annotations

import datetime
import re

ISO = "%Y-%m-%d"
STAMP = "%Y-%m-%d %H:%M"

_OFFSET_RE = re.compile(r"^([+-])(\d+)\s*([dwm])$", re.IGNORECASE)
_WEEKDAYS = {
    name: index
    for index, names in enumerate(
        (
            ("mon", "monday"),
            ("tue", "tues", "tuesday"),
            ("wed", "weds", "wednesday"),
            ("thu", "thur", "thurs", "thursday"),
            ("fri", "friday"),
            ("sat", "saturday"),
            ("sun", "sunday"),
        )
    )
    for name in names
}


def today() -> datetime.date:
    return datetime.date.today()


def stamp() -> str:
    """Now, as written into a comment heading."""
    return datetime.datetime.now().strftime(STAMP)


def parse(text: str) -> str:
    """Parse a date the way someone types it, return ISO.

    Accepts ``""`` (meaning unset), ISO, ``today``/``tomorrow``/``yesterday``,
    offsets like ``+3d``, ``+2w``, ``-1d``, ``+1m``, and weekday names, which
    mean the next such day strictly in the future.

    Raises ``ValueError`` on anything else.
    """
    text = text.strip().lower()
    if not text:
        return ""
    if text in ("today", "now"):
        return today().strftime(ISO)
    if text == "tomorrow":
        return (today() + datetime.timedelta(days=1)).strftime(ISO)
    if text == "yesterday":
        return (today() - datetime.timedelta(days=1)).strftime(ISO)

    offset = _OFFSET_RE.match(text)
    if offset:
        sign = -1 if offset.group(1) == "-" else 1
        count, unit = int(offset.group(2)), offset.group(3).lower()
        days = {"d": 1, "w": 7, "m": 30}[unit] * count
        return (today() + datetime.timedelta(days=sign * days)).strftime(ISO)

    if text in _WEEKDAYS:
        ahead = (_WEEKDAYS[text] - today().weekday()) % 7 or 7
        return (today() + datetime.timedelta(days=ahead)).strftime(ISO)

    try:
        return datetime.datetime.strptime(text, ISO).date().strftime(ISO)
    except ValueError:
        raise ValueError(
            f"{text!r} is not a date: use YYYY-MM-DD, today, tomorrow, +3d, +2w, or a weekday"
        ) from None


def is_valid(iso: str) -> bool:
    """Whether a stored value is a date we can read back (empty counts)."""
    if not iso.strip():
        return True
    try:
        datetime.datetime.strptime(iso.strip(), ISO)
    except ValueError:
        return False
    return True


def to_date(iso: str) -> datetime.date | None:
    try:
        return datetime.datetime.strptime(iso.strip(), ISO).date()
    except ValueError:
        return None


def is_overdue(iso: str, *, ref: datetime.date | None = None) -> bool:
    due = to_date(iso)
    return due is not None and due < (ref or today())


def is_today(iso: str, *, ref: datetime.date | None = None) -> bool:
    return to_date(iso) == (ref or today())


def days_until(iso: str, *, ref: datetime.date | None = None) -> int | None:
    due = to_date(iso)
    return None if due is None else (due - (ref or today())).days
