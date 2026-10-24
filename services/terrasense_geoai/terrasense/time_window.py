from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from terrasense.schemas import ParsedIntent


@dataclass
class LocalWindow:
    start: datetime
    end: datetime
    timezone: str


def _hour_range(day: datetime, start_h: int, end_h: int) -> tuple[datetime, datetime]:
    s = day.replace(hour=start_h, minute=0, second=0, microsecond=0)
    e = day.replace(hour=end_h, minute=0, second=0, microsecond=0)
    if e <= s:
        e += timedelta(days=1)
    return s, e


def resolve_window(
    intent: ParsedIntent,
    timezone: str,
    now: datetime | None = None,
) -> LocalWindow:
    """Resolve relative phrases using the *location* timezone and actual now."""
    tz = ZoneInfo(timezone)
    now_local = (now or datetime.now(tz)).astimezone(tz)
    blob = " ".join(
        x for x in [intent.date_text, intent.time_window_text, ""] if x
    ).lower()

    day = now_local
    if "day after tomorrow" in blob:
        day = now_local + timedelta(days=2)
    elif "tomorrow" in blob:
        day = now_local + timedelta(days=1)
    elif "this weekend" in blob or "weekend" in blob:
        # Saturday of this week, or next Saturday if already Sunday evening
        weekday = now_local.weekday()
        if weekday == 6:  # Sunday
            if "sunday" not in blob:
                day = now_local  # still Sunday
            else:
                day = now_local
        else:
            day = now_local + timedelta(days=(5 - weekday))  # Saturday
        if "sunday" in blob:
            weekday = now_local.weekday()
            day = now_local + timedelta(days=(6 - weekday) % 7)
            if day.date() < now_local.date():
                day += timedelta(days=7)

    start_h, end_h = 6, 20
    if "after work" in blob:
        start_h, end_h = 17, 20
    elif "morning" in blob:
        start_h, end_h = 6, 10
    elif "afternoon" in blob:
        start_h, end_h = 12, 17
    elif "evening" in blob or "sunset" in blob:
        start_h, end_h = 16, 20
    elif "night" in blob:
        start_h, end_h = 19, 22
    elif "this weekend" in blob or ( "weekend" in blob and "sunday" not in blob):
        start_h, end_h = 7, 18

    start, end = _hour_range(day, start_h, end_h)
    # "today" remaining hours
    if "tomorrow" not in blob and "weekend" not in blob and day.date() == now_local.date():
        if start < now_local:
            start = now_local.replace(minute=0, second=0, microsecond=0)
            if now_local.minute > 0 or now_local.second > 0:
                start = start + timedelta(hours=1)
        if end <= start:
            end = start + timedelta(hours=3)

    if "this weekend" in blob:
        weekday = now_local.weekday()
        sat = now_local + timedelta(days=(5 - weekday) % 7)
        if weekday == 6:
            sat = now_local  # it's Sunday — window is remaining Sunday
            start, end = _hour_range(sat, 7, 18)
            if start < now_local:
                start = now_local.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        else:
            if weekday == 5:
                sat = now_local
            start, end = _hour_range(sat, 7, 18)
            end = (sat + timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)

    return LocalWindow(start=start, end=end, timezone=timezone)


def select_hours(hours_local_iso: list[str], window: LocalWindow, tz_name: str, limit: int) -> list[str]:
    tz = ZoneInfo(tz_name)
    picked: list[str] = []
    for t in hours_local_iso:
        try:
            dt = datetime.fromisoformat(t)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=tz)
            else:
                dt = dt.astimezone(tz)
        except ValueError:
            continue
        if window.start <= dt < window.end:
            picked.append(t)
        if len(picked) >= limit:
            break
    return picked
