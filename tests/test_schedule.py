"""
Tests for the pricing engine.  Run with:

    .venv\\Scripts\\python.exe -m unittest discover -s tests -v

Everything is pinned to explicit UTC instants, so the suite never depends on
when or where it is run.  The 2026-09-23 case is a golden test: it reproduces
the reference design's table exactly (the screenshot was taken Wed 3:30 PM
GMT+5:30 with "3h 40m left" — the flip is Thu 06:30).
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import schedule as S  # noqa: E402

UTC = timezone.utc

#: A fixed, deliberately small holiday set so tests never depend on holidays.json.
HOLIDAYS = {"2026-10-01": "国庆节 (National Day)", "2026-09-25": "中秋节 (Mid-Autumn Festival)"}


def at(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp).replace(tzinfo=UTC)


class PeakRule(unittest.TestCase):
    """Peak = 01:00-04:00 + 06:00-10:00 UTC, Mon-Fri (2026-09-24 is a Thursday)."""

    def test_peak_window_boundaries_are_half_open(self):
        cases = {
            "2026-09-24T00:59": False,
            "2026-09-24T01:00": True,
            "2026-09-24T03:59": True,
            "2026-09-24T04:00": False,
            "2026-09-24T05:59": False,
            "2026-09-24T06:00": True,
            "2026-09-24T09:59": True,
            "2026-09-24T10:00": False,
            "2026-09-24T23:59": False,
        }
        for stamp, expected in cases.items():
            with self.subTest(stamp=stamp):
                self.assertIs(S.is_peak(at(stamp), HOLIDAYS), expected)

    def test_weekends_are_off_peak_all_day(self):
        for stamp in ("2026-09-26T01:30", "2026-09-26T07:30", "2026-09-27T02:00", "2026-09-27T09:00"):
            with self.subTest(stamp=stamp):
                self.assertFalse(S.is_peak(at(stamp), HOLIDAYS))

    def test_chinese_public_holiday_is_off_peak_all_day(self):
        # 2026-10-01 is a Thursday, so both windows would normally be peak.
        self.assertTrue(S.is_peak(at("2026-09-24T02:00"), HOLIDAYS))
        self.assertFalse(S.is_peak(at("2026-10-01T02:00"), HOLIDAYS))
        self.assertFalse(S.is_peak(at("2026-10-01T07:00"), HOLIDAYS))
        # ...and the day after the holiday is peak again.
        self.assertTrue(S.is_peak(at("2026-10-08T02:00"), {"2026-10-01": "国庆节"}))

    def test_holiday_rule_is_ignored_when_no_calendar_is_loaded(self):
        self.assertTrue(S.is_peak(at("2026-10-01T02:00"), {}))

    def test_friday_and_monday_edges(self):
        self.assertTrue(S.is_peak(at("2026-09-25T09:59"), {}))   # Friday
        self.assertFalse(S.is_peak(at("2026-09-26T01:00"), {}))  # Saturday
        self.assertTrue(S.is_peak(at("2026-09-28T01:00"), {}))   # Monday


class Windows(unittest.TestCase):
    def test_windows_merge_into_maximal_spans(self):
        # Spans are deliberately NOT clipped to the queried range: the window
        # you are inside has to be able to show its full span.
        found = S.windows(at("2026-09-24T00:00"), at("2026-09-25T00:00"), {})
        self.assertEqual(
            [(w.start.isoformat(), w.end.isoformat(), w.mode) for w in found],
            [
                ("2026-09-23T10:00:00+00:00", "2026-09-24T01:00:00+00:00", "off-peak"),
                ("2026-09-24T01:00:00+00:00", "2026-09-24T04:00:00+00:00", "peak"),
                ("2026-09-24T04:00:00+00:00", "2026-09-24T06:00:00+00:00", "off-peak"),
                ("2026-09-24T06:00:00+00:00", "2026-09-24T10:00:00+00:00", "peak"),
                ("2026-09-24T10:00:00+00:00", "2026-09-25T01:00:00+00:00", "off-peak"),
            ],
        )

    def test_weekend_collapses_into_one_long_off_peak_window(self):
        # Fri 10:00 UTC -> Mon 01:00 UTC: Sat/Sun are off-peak all day and the
        # Monday run continues until the first peak window opens at 01:00 UTC.
        window = S.current_window(at("2026-09-25T10:00"), {})
        self.assertEqual(window.mode, "off-peak")
        self.assertEqual(window.start, at("2026-09-25T10:00"))
        self.assertEqual(window.end, at("2026-09-28T01:00"))
        self.assertEqual(window.seconds, 63 * 3600)

    def test_holiday_week_folds_into_one_closed_window(self):
        # Regression: the scan range used to cut these runs off early, which
        # reported a Friday-evening flip as "Monday 00:00" instead of 01:00.
        holidays = S.load_holidays()
        national_day = S.current_window(at("2026-10-04T02:00"), holidays)
        self.assertEqual(national_day.mode, "off-peak")
        self.assertEqual(national_day.start, at("2026-09-30T10:00"))
        self.assertEqual(national_day.end, at("2026-10-08T01:00"))
        self.assertEqual(national_day.seconds, 183 * 3600)

        spring_festival = S.current_window(at("2026-02-17T02:00"), holidays)
        self.assertEqual(spring_festival.start, at("2026-02-13T10:00"))
        self.assertEqual(spring_festival.end, at("2026-02-24T01:00"))
        self.assertEqual(spring_festival.seconds, 255 * 3600)

    def test_current_window_contains_the_moment(self):
        moment = at("2026-09-24T07:15")
        window = S.current_window(moment, {})
        self.assertTrue(window.start <= moment < window.end)
        self.assertEqual((window.start.hour, window.end.hour), (6, 10))

    def test_next_change_flips_the_mode(self):
        when, peak_after = S.next_change(at("2026-09-24T07:15"), {})
        self.assertEqual(when, at("2026-09-24T10:00"))
        self.assertFalse(peak_after)

        when, peak_after = S.next_change(at("2026-09-24T11:00"), {})
        self.assertEqual(when, at("2026-09-25T01:00"))
        self.assertTrue(peak_after)


class Formatting(unittest.TestCase):
    def test_clock_and_duration_and_countdown(self):
        self.assertEqual(S.fmt_clock(at("2026-09-24T06:30")), "06:30 AM")
        self.assertEqual(S.fmt_clock(at("2026-09-24T15:30")), "03:30 PM")
        self.assertEqual(S.fmt_clock(at("2026-09-24T00:05")), "12:05 AM")
        self.assertEqual(S.fmt_clock(at("2026-09-24T12:00")), "12:00 PM")

        self.assertEqual(S.fmt_duration(4 * 3600), "4h")
        self.assertEqual(S.fmt_duration(3 * 3600 + 40 * 60), "3h 40m")
        self.assertEqual(S.fmt_duration(45 * 60), "45m")
        self.assertEqual(S.fmt_duration(66 * 3600), "2d 18h")
        self.assertEqual(S.fmt_duration(90), "1m")

        self.assertEqual(S.fmt_countdown(3 * 3600 + 40 * 60 + 45), "3:40:45")
        self.assertEqual(S.fmt_countdown(0), "0:00:00")
        self.assertEqual(S.fmt_countdown(2 * 86400 + 15 * 3600 + 20 * 60 + 45), "2d 15:20:45")

    def test_tz_abbreviation(self):
        zone, canonical, label = S.resolve_tz("Asia/Kolkata")
        self.assertEqual(label, "Indian Standard Time (UTC+5:30)")
        self.assertEqual(canonical, "Asia/Kolkata")
        self.assertEqual(
            S.tz_abbreviation(at("2026-09-24T10:00").astimezone(zone), canonical), "GMT+5:30"
        )
        qatar, canonical, _ = S.resolve_tz("Asia/Qatar")
        self.assertEqual(S.tz_abbreviation(at("2026-09-24T10:00").astimezone(qatar), canonical), "GMT+3")
        # only the explicit UTC option reads "UTC"; a zero-offset *zone* still
        # reads as an offset so the label never flips names twice a year
        self.assertEqual(S.tz_abbreviation(at("2026-09-24T10:00"), "UTC"), "UTC")
        self.assertEqual(S.tz_abbreviation(at("2026-09-24T10:00"), "Europe/London"), "GMT+0")


class LondonDst(unittest.TestCase):
    """The UK option must follow BST/GMT, not a frozen offset."""

    def test_summer_is_gmt_plus_one(self):
        state = S.build_state(at("2026-07-01T07:00"), "Europe/London", {})
        self.assertEqual(state["tz"]["abbr"], "GMT+1")
        self.assertEqual(state["now"]["clock"], "08:00 AM")
        self.assertEqual(state["mode"], "peak")

    def test_winter_is_gmt(self):
        state = S.build_state(at("2026-12-02T07:00"), "Europe/London", {})
        self.assertEqual(state["tz"]["abbr"], "GMT+0")
        self.assertEqual(state["now"]["clock"], "07:00 AM")
        self.assertEqual(state["mode"], "peak")


class Payload(unittest.TestCase):
    def setUp(self):
        # Wed 2026-09-23 15:30 in India == 10:00 UTC: exactly the reference design.
        self.state = S.build_state(at("2026-09-23T10:00"), "Asia/Kolkata", HOLIDAYS)

    def test_headline_matches_the_reference_design(self):
        self.assertEqual(self.state["mode"], "off-peak")
        self.assertEqual(self.state["mode_label"], "OFF-PEAK")
        self.assertEqual(self.state["now"]["clock"], "03:30 PM")
        self.assertEqual(self.state["countdown"]["text"], "15:00:00")
        self.assertEqual(self.state["next"]["mode"], "peak")
        self.assertEqual(self.state["next"]["mode_name"], "Peak")
        self.assertEqual(self.state["next"]["mode_label"], "PEAK")
        self.assertEqual(
            self.state["next"]["line"], "Peak starts 06:30 AM GMT+5:30"
        )
        self.assertEqual(self.state["tz"]["abbr"], "GMT+5:30")

    def test_window_table_matches_the_reference_design(self):
        rows = self.state["rows"]
        self.assertEqual(len(rows), 4)
        self.assertEqual(
            [(r["kind"], r["start"], r["end"], r["right"]) for r in rows],
            [
                ("off-peak", "WED 03:30 PM", "THU 06:30 AM", "15h left"),
                ("peak", "06:30 AM", "09:30 AM", "3h"),
                ("off-peak", "09:30 AM", "11:30 AM", "2h"),
                ("peak", "11:30 AM", "03:30 PM", "4h"),
            ],
        )
        self.assertEqual([r["is_now"] for r in rows], [True, False, False, False])

    def test_day_bar_segments_cover_the_local_day(self):
        segments = self.state["segments"]
        self.assertAlmostEqual(sum(s["width_pct"] for s in segments), 100.0, places=3)
        self.assertEqual(
            [(s["kind"], round(s["start_pct"], 2), round(s["width_pct"], 2)) for s in segments],
            [
                ("off-peak", 0.0, 27.08),
                ("peak", 27.08, 12.5),
                ("off-peak", 39.58, 8.33),
                ("peak", 47.92, 16.67),
                ("off-peak", 64.58, 35.42),
            ],
        )
        self.assertEqual(self.state["hours"], {"peak": 7.0, "off_peak": 17.0})

    def test_current_row_keeps_its_true_span_through_a_holiday_week(self):
        state = S.build_state(at("2026-10-04T02:00"), "Asia/Kolkata", S.load_holidays())
        self.assertEqual(len(state["rows"]), 1)
        self.assertEqual(state["rows"][0]["start"], "WED 03:30 PM")
        self.assertEqual(state["rows"][0]["end"], "THU 06:30 AM")
        self.assertEqual(state["rows"][0]["total"], "7d 15h")
        self.assertEqual(state["holiday"]["today"], "国庆节 (National Day)")

    def test_holiday_is_reported(self):
        state = S.build_state(at("2026-09-25T02:00"), "Asia/Kolkata", HOLIDAYS)
        self.assertEqual(state["holiday"]["today"], "中秋节 (Mid-Autumn Festival)")
        self.assertEqual(state["mode"], "off-peak")
        self.assertEqual(state["rows"][0]["is_now"], True)

    def test_mode_labels_are_just_the_mode(self):
        self.assertEqual(S.mode_label(True), "PEAK")
        self.assertEqual(S.mode_label(False), "OFF-PEAK")

    def test_payload_is_json_serialisable(self):
        import json

        blob = json.dumps(S.build_state(at("2026-09-24T07:00"), "auto", HOLIDAYS))
        self.assertIn('"countdown"', blob)

    def test_every_choice_resolves(self):
        for entry in S.TIMEZONES:
            with self.subTest(tz=entry["id"]):
                state = S.build_state(at("2026-09-24T07:00"), entry["id"], {})
                self.assertTrue(state["tz"]["abbr"])
                self.assertIn(state["mode"], ("peak", "off-peak"))

    def test_countdown_never_goes_negative_across_a_whole_week(self):
        moment = at("2026-09-20T00:00")
        for _ in range(0, 7 * 24 * 60, 11):
            state = S.build_state(moment, "Asia/Qatar", HOLIDAYS)
            self.assertGreaterEqual(state["countdown"]["seconds"], 0)
            self.assertAlmostEqual(
                sum(s["width_pct"] for s in state["segments"]), 100.0, places=3
            )
            moment += timedelta(minutes=7)


class RealCalendar(unittest.TestCase):
    """The shipped holidays.json must load and agree with DeepSeek's rule."""

    def test_shipped_calendar_loads(self):
        holidays = S.load_holidays()
        self.assertTrue(holidays, "holidays.json is missing or empty")
        self.assertIn("2026-10-01", holidays)

    def test_national_day_week_is_off_peak(self):
        holidays = S.load_holidays()
        for stamp in ("2026-10-01T02:00", "2026-10-02T07:00", "2026-10-05T02:00"):
            with self.subTest(stamp=stamp):
                self.assertFalse(S.is_peak(at(stamp), holidays))


if __name__ == "__main__":
    unittest.main(verbosity=2)
