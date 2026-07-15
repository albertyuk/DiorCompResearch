"""Deck date formatting per the Decision Record.

`JULY 9TH`, ranges `JULY 2ND – 3RD` (en dash), ongoing `JULY 1ST – TBD`.
Ordinal suffixes are rendered superscript in the PPTX; here we produce
(text, is_superscript) run segments so the renderer can style them.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

CST = ZoneInfo("Asia/Shanghai")

MONTHS = ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY",
          "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"]


def ordinal_suffix(day: int) -> str:
    if 11 <= day % 100 <= 13:
        return "TH"
    return {1: "ST", 2: "ND", 3: "RD"}.get(day % 10, "TH")


def month_name(d: date) -> str:
    return MONTHS[d.month - 1]


def date_runs(start: date, end: date | None, ongoing: bool = False) -> list[tuple[str, bool]]:
    """Segments (text, superscript) for the DATE cell.

    - single day:            JULY 9 + ^TH
    - same-month range:      JULY 2 + ^ND + " – 3" + ^RD
    - cross-month range:     JUNE 28 + ^TH + " – JULY 3" + ^RD
    - ongoing at month end:  JULY 1 + ^ST + " – TBD"
    """
    runs: list[tuple[str, bool]] = [(f"{month_name(start)} {start.day}", False),
                                    (ordinal_suffix(start.day), True)]
    if ongoing:
        runs.append((" – TBD", False))
        return runs
    if end is None or end == start:
        return runs
    if end.month == start.month and end.year == start.year:
        runs.append((f" – {end.day}", False))
    else:
        runs.append((f" – {month_name(end)} {end.day}", False))
    runs.append((ordinal_suffix(end.day), True))
    return runs


def date_text(start: date, end: date | None, ongoing: bool = False) -> str:
    return "".join(t for t, _ in date_runs(start, end, ongoing))


def parse_iso(s: str | None) -> date | None:
    if not s:
        return None
    return date.fromisoformat(s[:10])


def month_bounds(month: str) -> tuple[datetime, datetime]:
    """month 'YYYY-MM' -> [start, end) as CST-aware datetimes."""
    y, m = int(month[:4]), int(month[5:7])
    start = datetime(y, m, 1, tzinfo=CST)
    end = datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=CST)
    return start, end


def previous_month(today: date | None = None) -> str:
    today = today or datetime.now(CST).date()
    first = today.replace(day=1)
    prev_last = first - timedelta(days=1)
    return f"{prev_last.year:04d}-{prev_last.month:02d}"


def deck_month_token(month: str) -> str:
    """'2026-07' -> 'JULY' for the output filename."""
    return MONTHS[int(month[5:7]) - 1]
