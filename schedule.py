"""
DeepSeek off-peak pricing engine.

This is THE single source of truth for "is it peak right now?", "when does the
price flip?" and "what do the next 24 hours look like".  The widget UI renders
the JSON produced by :func:`build_state`, and the tray icon / toast
notifications are driven by :func:`is_peak` — so the countdown in the window can
never disagree with a notification.

THE RULES (verbatim from https://api-docs.deepseek.com/quick_start/pricing):

    "Off-peak rates are half of the peak rates. Peak hours are 01:00 - 04:00
     and 06:00 - 10:00 UTC, Monday through Friday, excluding Chinese public
     holidays. All other hours are off-peak, including weekends and Chinese
     public holidays in full."

Consequences the code relies on:

* the two peak windows are anchored to UTC, never to the viewer's timezone, so
  changing the timezone moves the *labels*, not the *pricing*;
* "Monday through Friday" is the UTC weekday;
* Chinese public holidays are calendar dates in China Standard Time, so a
  holiday is a CST (UTC+8) day;
* off-peak is half price, therefore peak is 2x the off-peak rate.

Note on the CST-vs-UTC holiday question: both peak windows (01:00-04:00 and
06:00-10:00 UTC) fall inside the same CST calendar day (09:00-12:00 and
14:00-18:00 CST), and the 16:00-24:00 UTC remainder of a UTC day contains no
peak window at all.  So "is today a holiday" gives the identical answer whether
the date is taken in CST or UTC.  CST is used anyway because it is the calendar
the data is published on.

Pure stdlib: no network access, no third-party imports (tzdata is needed on
Windows only because Windows ships no IANA database).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# --------------------------------------------------------------------------- #
# Facts
# --------------------------------------------------------------------------- #

#: (start_hour, end_hour) in UTC — half-open intervals [start, end).
PEAK_WINDOWS_UTC: tuple[tuple[int, int], ...] = ((1, 4), (6, 10))

#: Off-peak is half price, so peak costs twice the off-peak rate.
OFF_PEAK_MULTIPLIER = 0.5

#: Peak (and every boundary) only exists Monday-Friday.  datetime.weekday():
#: Monday == 0 ... Sunday == 6.
PEAK_WEEKDAYS = (0, 1, 2, 3, 4)

#: Chinese public holidays are published as CST dates.  China has no DST, so a
#: plain +08:00 offset is exact (and avoids depending on the IANA db here).
HOLIDAY_TZ = timezone(timedelta(hours=8), "CST")

#: Any UTC instant at one of these hours is a possible price flip, so these are
#: the only instants where the state has to be re-evaluated.
BOUNDARY_HOURS = (0, 1, 4, 6, 10, 24)

#: How far ahead the price-window table looks (matches the reference design).
HORIZON = timedelta(hours=24)

#: The timeline is sliced from before the requested range until well after it,
#: so that every returned window is bounded by a *real* price change instead of
#: by the edge of the scan.  The longest run of off-peak time that the holiday
#: rule can produce is around 11 days (Spring Festival 2026 runs Fri 13 Feb
#: 10:00Z -> Tue 24 Feb 01:00Z = 255 h), so both buffers are comfortably larger.
SCAN_BEFORE = timedelta(days=20)
SCAN_AFTER = timedelta(days=24)

DEFAULT_HOLIDAY_FILE = Path(__file__).resolve().parent / "holidays.json"

#: Selectable timezones.  The first one is the default.  The chips show the
#: standard abbreviation, never a country or city name: `short` is what fits on
#: the card and `label` is the full name used for tooltips and the tray.
TIMEZONES: tuple[dict[str, str], ...] = (
    {"id": "auto", "label": "System", "short": "Auto"},
    {"id": "Asia/Qatar", "label": "Arabian Standard Time (UTC+3)", "short": "AST"},
    {"id": "Asia/Kolkata", "label": "Indian Standard Time (UTC+5:30)", "short": "IST"},
    {"id": "Europe/London", "label": "Greenwich Mean Time (GMT / BST)", "short": "GMT"},
    {"id": "Asia/Shanghai", "label": "UTC+8 (no daylight saving)", "short": "UTC+8"},
    {"id": "UTC", "label": "Coordinated Universal Time", "short": "UTC"},
)

#: Windows timezone ids -> IANA, for the "System" option.  The OS exposes a
#: Windows id; without this map we could only fall back to a fixed offset,
#: which would ignore a DST change happening inside the 24 h window.
_WINDOWS_TZ_MAP = {
    "Arab Standard Time": "Asia/Qatar",
    "Arabian Standard Time": "Asia/Dubai",
    "India Standard Time": "Asia/Kolkata",
    "Sri Lanka Standard Time": "Asia/Colombo",
    "GMT Standard Time": "Europe/London",
    "UTC": "UTC",
    "UTC+12": "Pacific/Auckland",
    "China Standard Time": "Asia/Shanghai",
    "Singapore Standard Time": "Asia/Singapore",
    "Tokyo Standard Time": "Asia/Tokyo",
    "Korea Standard Time": "Asia/Seoul",
    "W. Europe Standard Time": "Europe/Berlin",
    "Central Europe Standard Time": "Europe/Budapest",
    "Romance Standard Time": "Europe/Paris",
    "E. Europe Standard Time": "Europe/Chisinau",
    "Turkey Standard Time": "Europe/Istanbul",
    "Pakistan Standard Time": "Asia/Karachi",
    "Bangladesh Standard Time": "Asia/Dhaka",
    "SE Asia Standard Time": "Asia/Bangkok",
    "Egypt Standard Time": "Africa/Cairo",
    "South Africa Standard Time": "Africa/Johannesburg",
    "Eastern Standard Time": "America/New_York",
    "Central Standard Time": "America/Chicago",
    "Mountain Standard Time": "America/Denver",
    "Pacific Standard Time": "America/Los_Angeles",
}


# --------------------------------------------------------------------------- #
# Holidays
# --------------------------------------------------------------------------- #


def load_holidays(path: str | Path | None = None) -> dict[str, str]:
    """Return ``{"YYYY-MM-DD": "<name>"}`` for every CN public holiday we know.

    The file is generated by ``tools/build_holidays.py`` from the State Council
    announcements (via the holiday-cn dataset).  A missing/corrupt file simply
    disables the holiday rule instead of breaking the widget.
    """
    path = Path(path) if path else DEFAULT_HOLIDAY_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    days = raw.get("days") or {}
    if isinstance(days, dict):
        return {str(k): _holiday_name(v) for k, v in days.items()}
    # tolerate a plain list of dates
    return {str(d): "" for d in days}


def _holiday_name(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        name = str(value.get("name") or "")
        english = value.get("en")
        return f"{name} ({english})" if english else name
    return ""


def holiday_on(moment: datetime, holidays: dict[str, str]) -> str | None:
    """Name of the CN public holiday covering ``moment``, else ``None``."""
    if not holidays:
        return None
    return holidays.get(moment.astimezone(HOLIDAY_TZ).date().isoformat())


# --------------------------------------------------------------------------- #
# The schedule
# --------------------------------------------------------------------------- #


def is_peak(moment: datetime, holidays: dict[str, str] | None = None) -> bool:
    """True when DeepSeek charges peak (full-price) rates at ``moment``."""
    moment = _aware_utc(moment)
    if holiday_on(moment, holidays or {}):
        return False
    if moment.weekday() not in PEAK_WEEKDAYS:
        return False
    minutes = moment.hour * 60 + moment.minute
    return any(start * 60 <= minutes < end * 60 for start, end in PEAK_WINDOWS_UTC)


@dataclass(frozen=True)
class Window:
    """A maximal span with a constant price, in UTC."""

    start: datetime
    end: datetime
    peak: bool

    @property
    def seconds(self) -> float:
        return (self.end - self.start).total_seconds()

    @property
    def mode(self) -> str:
        return "peak" if self.peak else "off-peak"


def _aware_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _boundaries(first_day: date, days: int) -> list[datetime]:
    """Every UTC instant at which the price *could* change, from ``first_day``."""
    out: list[datetime] = []
    for offset in range(days):
        midnight = datetime.combine(
            first_day + timedelta(days=offset), time.min, tzinfo=timezone.utc
        )
        out.extend(midnight + timedelta(hours=hour) for hour in BOUNDARY_HOURS)
    return sorted(set(out))


def windows(
    start: datetime,
    end: datetime,
    holidays: dict[str, str] | None = None,
) -> list[Window]:
    """Maximal constant-price windows overlapping ``[start, end)``.

    Built by slicing the timeline at every boundary instant and testing the
    middle of each slice — the state cannot change between two adjacent
    boundaries, so this is exact rather than sampled.

    Window spans are never clipped to ``[start, end)`` and never truncated by
    the scan: the scan is widened by :data:`SCAN_BEFORE` / :data:`SCAN_AFTER`
    so the first and last returned spans end where the price actually changes.
    That is what makes the "you are here" row show its full span, and it is why
    a Friday-evening +05:30 reader sees the real Monday 06:30 flip rather than
    a Monday-midnight cut-off.
    """
    start, end = _aware_utc(start), _aware_utc(end)
    first_day = (start - SCAN_BEFORE).date()
    days = ((end + SCAN_AFTER).date() - first_day).days + 1
    marks = _boundaries(first_day, days)

    merged: list[Window] = []
    for a, b in zip(marks, marks[1:]):
        peak = is_peak(a + (b - a) / 2, holidays)
        if merged and merged[-1].peak == peak and merged[-1].end == a:
            merged[-1] = replace(merged[-1], end=b)
        else:
            merged.append(Window(a, b, peak))

    return [w for w in merged if w.end > start and w.start < end]


def current_window(moment: datetime, holidays: dict[str, str] | None = None) -> Window:
    """The constant-price window that contains ``moment`` (with true bounds)."""
    moment = _aware_utc(moment)
    for window in windows(moment - timedelta(days=1), moment + timedelta(days=1), holidays):
        if window.start <= moment < window.end:
            return window
    raise AssertionError("no window contains the given moment")  # pragma: no cover


def next_change(
    moment: datetime, holidays: dict[str, str] | None = None
) -> tuple[datetime, bool]:
    """``(when the price next flips, the peak flag after the flip)``."""
    window = current_window(moment, holidays)
    return window.end, not window.peak


# --------------------------------------------------------------------------- #
# Timezones
# --------------------------------------------------------------------------- #


def _system_tz() -> tuple[tzinfo, str | None]:
    """Best-effort IANA zone for the OS clock (Windows registry id -> IANA)."""
    key: str | None = None
    try:
        import winreg

        with winreg.OpenKey(  # type: ignore[attr-defined]
            winreg.HKEY_LOCAL_MACHINE,  # type: ignore[attr-defined]
            r"SYSTEM\CurrentControlSet\Control\TimeZoneInformation",
        ) as handle:
            key = str(winreg.QueryValueEx(handle, "TimeZoneKeyName")[0])  # type: ignore[attr-defined]
    except Exception:
        key = None

    iana = _WINDOWS_TZ_MAP.get(key or "")
    if iana:
        try:
            return ZoneInfo(iana), iana
        except (ZoneInfoNotFoundError, ValueError):
            pass

    # Fall back to the offset the OS is using right now (no DST awareness).
    return datetime.now().astimezone().tzinfo or timezone.utc, None


def resolve_tz(tz_id: str) -> tuple[tzinfo, str, str]:
    """``(tzinfo, canonical id, label)`` for a selectable timezone id."""
    wanted = (tz_id or "auto").strip()
    if wanted.lower() == "auto":
        zone, iana = _system_tz()
        return zone, (iana or "auto"), timezone_label("auto", iana)
    if wanted.upper() == "UTC":
        return timezone.utc, "UTC", "UTC"
    try:
        return ZoneInfo(wanted), wanted, timezone_label(wanted)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("Asia/Kolkata"), "Asia/Kolkata", timezone_label("Asia/Kolkata")


def timezone_label(tz_id: str, fallback_iana: str | None = None) -> str:
    for entry in TIMEZONES:
        if entry["id"] == tz_id:
            return entry["label"]
    if fallback_iana:
        return fallback_iana.split("/")[-1].replace("_", " ")
    return tz_id.split("/")[-1].replace("_", " ")


def _catalog_id(tz_id: str) -> str:
    """The catalog id the UI should highlight for a requested tz id."""
    wanted = (tz_id or "auto").strip()
    if wanted.lower() == "auto":
        return "auto"
    for entry in TIMEZONES:
        if entry["id"].lower() == wanted.lower():
            return entry["id"]
    return wanted


def tz_abbreviation(local: datetime, canonical_id: str | None = None) -> str:
    """``GMT+5:30`` style label — matches the reference design and needs no tz
    abbreviation tables (``IST``/``BST`` would be ambiguous anyway).

    Only the explicit UTC option reads ``UTC``; a real zone that happens to sit
    on zero offset (London in winter) reads ``GMT+0`` so the label stays a
    consistent offset rather than flipping names twice a year.
    """
    offset = local.utcoffset() or timedelta(0)
    if canonical_id == "UTC" and offset == timedelta(0):
        return "UTC"
    total = int(offset.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    hours, minutes = divmod(total // 60, 60)
    return f"GMT{sign}{hours}:{minutes:02d}" if minutes else f"GMT{sign}{hours}"


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #


def fmt_clock(local: datetime) -> str:
    """``03:30 PM`` — 12-hour clock with a leading zero, as in the design."""
    hour = local.hour % 12 or 12
    return f"{hour:02d}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}"


def fmt_weekday(local: datetime, upper: bool = False) -> str:
    name = local.strftime("%a")
    return name.upper() if upper else name


def fmt_duration(seconds: float) -> str:
    """``4h`` / ``3h 40m`` / ``2d 15h`` / ``45m`` / ``30s``."""
    total = max(0, int(seconds))
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days}d {hours}h" if hours else f"{days}d"
    if hours:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    if minutes:
        return f"{minutes}m"
    return f"{total}s"


def fmt_countdown(seconds: float) -> str:
    """``3:40:45`` — and ``2d 15:20:45`` once it passes a day."""
    total = max(0, int(seconds))
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f"{days}d {hours}:{minutes:02d}:{secs:02d}"
    return f"{hours}:{minutes:02d}:{secs:02d}"


def mode_label(peak: bool) -> str:
    """Badge wording.  Deliberately just the mode: the card is read at a glance
    and the rate itself is documented rather than restated on the widget."""
    return "PEAK" if peak else "OFF-PEAK"


def mode_name(peak: bool) -> str:
    return "Peak" if peak else "Off-peak"


def mode_key(peak: bool) -> str:
    """Machine value used as a CSS hook / for comparisons."""
    return "peak" if peak else "off-peak"


def next_change_phrase(peak_after: bool) -> str:
    return "Peak starts" if peak_after else "Off-peak starts"


# --------------------------------------------------------------------------- #
# The UI payload
# --------------------------------------------------------------------------- #


def build_state(
    now: datetime | None = None,
    tz_id: str = "auto",
    holidays: dict[str, str] | None = None,
    horizon: timedelta = HORIZON,
) -> dict:
    """Everything the widget draws, as JSON-serialisable data.

    ``now`` is injectable so the whole payload is testable at a pinned instant.
    """
    now = _aware_utc(now or datetime.now(timezone.utc))
    holidays = load_holidays() if holidays is None else holidays

    zone, canonical_id, label = resolve_tz(tz_id)
    local_now = now.astimezone(zone)
    abbr = tz_abbreviation(local_now, canonical_id)
    selected = _catalog_id(tz_id)

    window = current_window(now, holidays)
    flip, peak_after = window.end, not window.peak
    flip_local = flip.astimezone(zone)
    remaining = (flip - now).total_seconds()

    # "Peak starts 04:00 AM" is a lie by omission when the window still has days to
    # run — a weekend plus a Chinese holiday makes a 3-day off-peak stretch, and the
    # line then reads like a broken countdown.  Say which day, and drop the offset
    # there: the date is the part that answers the question (the tooltip keeps both).
    days_away = (flip_local.date() - local_now.date()).days
    if days_away <= 0:
        day_short, day_long, day_suffix = "", "", f" {abbr}"
    elif days_away == 1:
        day_short = day_long = "tomorrow, "
        day_suffix = f" {abbr}"
    else:
        # The card only has room for the weekday; the full date goes in `line`,
        # which the tray, the notifications and the tooltip use.
        day_short = f"{fmt_weekday(flip_local)} "
        day_long = f"{fmt_weekday(flip_local)} {flip_local.day} {flip_local.strftime('%b')}, "
        day_suffix = ""
    next_line = (
        f"{next_change_phrase(peak_after)} {day_long}{fmt_clock(flip_local)}{day_suffix}"
    )
    next_day = f"{day_short}{fmt_clock(flip_local)}"

    return {
        "generated_at": _iso(now),
        "epoch_ms": int(now.timestamp() * 1000),
        "tz": {
            "id": canonical_id,
            "selected": selected,
            "label": label,
            "abbr": abbr,
            "offset_minutes": int((local_now.utcoffset() or timedelta(0)).total_seconds() // 60),
            "choices": [dict(entry) for entry in TIMEZONES],
        },
        "now": {
            "seconds_of_day": local_now.hour * 3600 + local_now.minute * 60 + local_now.second,
            "clock": fmt_clock(local_now),
            "weekday": fmt_weekday(local_now),
            "date": local_now.strftime("%d %b %Y"),
            "date_short": local_now.strftime("%d %b"),
            "iso": local_now.isoformat(),
        },
        "mode": window.mode,
        "mode_label": mode_label(window.peak),
        "mode_name": mode_name(window.peak),
        "is_peak": window.peak,
        "window": {
            "start": _iso(window.start),
            "end": _iso(window.end),
            "start_local": fmt_clock(window.start.astimezone(zone)),
            "end_local": fmt_clock(window.end.astimezone(zone)),
            "duration": fmt_duration(window.seconds),
        },
        "countdown": {
            "seconds": int(remaining),
            "text": fmt_countdown(remaining),
            "suffix": "left",
            "long": remaining >= 86400,
        },
        "next": {
            "is_peak": peak_after,
            "mode": mode_key(peak_after),
            "mode_name": mode_name(peak_after),
            "mode_label": mode_label(peak_after),
            "phrase": next_change_phrase(peak_after),
            "clock": fmt_clock(flip_local),
            "weekday": fmt_weekday(flip_local),
            # What the card shows next to the clock: "Mon 04:00 AM", or the plain
            # clock with the offset when the change is today.
            "day": day_short,
            "day_clock": next_day,
            "abbr": abbr,
            "line": next_line,
        },
        "rows": _rows(now, zone, holidays, horizon),
        "segments": _segments(now, zone, holidays),
        "hours": _hour_totals(now, zone, holidays),
        "holiday": _holiday_info(now, holidays),
        "rules": {
            "peak_utc": ["01:00 – 04:00", "06:00 – 10:00"],
            "days": "Mon – Fri (UTC)",
            "discount": "50% off peak",
            "source": "api-docs.deepseek.com/quick_start/pricing",
            "note": "Weekends and Chinese public holidays are off-peak all day.",
        },
        "holiday_source": _holiday_source(),
    }


def _rows(
    now: datetime,
    zone: tzinfo,
    holidays: dict[str, str],
    horizon: timedelta,
) -> list[dict]:
    """The price-window table: the window you are in plus the next ``horizon``.

    A window is listed when it starts before ``now + horizon``; the one you are
    inside is always first because its end is still in the future.  The current
    row shows its *full* span (as the reference design does) and is annotated
    with the time remaining in it; later rows show their total duration.  The
    note drops the reference design's "now · " prefix because at widget width
    that prefix was enough to push the window's end time into an ellipsis — the
    row is marked by its colour and an accent bar in CSS instead.
    """
    rows: list[dict] = []
    for window in windows(now, now + horizon, holidays):
        start, end = window.start.astimezone(zone), window.end.astimezone(zone)
        is_now = window.start <= now < window.end
        crosses_day = start.date() != end.date()
        rows.append(
            {
                "kind": window.mode,
                "label": mode_name(window.peak),
                "start": _stamp(start, crosses_day),
                "end": _stamp(end, crosses_day),
                "total": fmt_duration(window.seconds),
                "is_now": is_now,
                "right": (
                    f"{fmt_duration((window.end - now).total_seconds())} left"
                    if is_now
                    else fmt_duration(window.seconds)
                ),
            }
        )
    return rows


def _stamp(local: datetime, with_weekday: bool) -> str:
    clock = fmt_clock(local)
    return f"{fmt_weekday(local, upper=True)} {clock}" if with_weekday else clock


def _segments(now: datetime, zone: tzinfo, holidays: dict[str, str]) -> list[dict]:
    """Peak/off-peak segments across the *local* day containing ``now``."""
    local_now = now.astimezone(zone)
    day_start = datetime.combine(local_now.date(), time.min, tzinfo=zone)
    day_end = day_start + timedelta(days=1)
    span = (day_end - day_start).total_seconds()

    segments: list[dict] = []
    for window in windows(day_start, day_end, holidays):
        clipped_start = max(window.start, day_start)
        clipped_end = min(window.end, day_end)
        segments.append(
            {
                "kind": window.mode,
                "start_pct": round((clipped_start - day_start).total_seconds() / span * 100, 4),
                "width_pct": round((clipped_end - clipped_start).total_seconds() / span * 100, 4),
            }
        )
    return segments


def _hour_totals(now: datetime, zone: tzinfo, holidays: dict[str, str]) -> dict:
    segments = _segments(now, zone, holidays)
    peak = sum(s["width_pct"] for s in segments if s["kind"] == "peak") / 100 * 24
    return {"peak": round(peak, 2), "off_peak": round(24 - peak, 2)}


def _holiday_info(now: datetime, holidays: dict[str, str]) -> dict:
    today = holiday_on(now, holidays)
    upcoming: list[dict] = []
    day = now.astimezone(HOLIDAY_TZ).date()
    for offset in range(1, 10):
        candidate = (day + timedelta(days=offset)).isoformat()
        if candidate in holidays:
            upcoming.append(
                {"date": candidate, "name": holidays[candidate], "in_days": offset}
            )
    return {"today": today, "upcoming": upcoming, "loaded": len(holidays)}


def _holiday_source() -> str:
    try:
        raw = json.loads(DEFAULT_HOLIDAY_FILE.read_text(encoding="utf-8"))
        return str(raw.get("source", ""))
    except (OSError, ValueError):
        return ""


def _iso(moment: datetime) -> str:
    return _aware_utc(moment).isoformat().replace("+00:00", "Z")


def summarise(state: dict) -> str:
    """One-line status, used for the tray tooltip."""
    return (
        f"{state['mode_name']} · {state['countdown']['text']} {state['countdown']['suffix']} "
        f"→ {state['next']['mode']} {state['next']['clock']} {state['next']['abbr']}"
    )


def iter_choices() -> Iterable[dict[str, str]]:
    return iter(TIMEZONES)


if __name__ == "__main__":  # manual smoke test: python schedule.py [tz]
    import sys

    wanted = sys.argv[1] if len(sys.argv) > 1 else "auto"
    print(json.dumps(build_state(tz_id=wanted), indent=2, ensure_ascii=False))
