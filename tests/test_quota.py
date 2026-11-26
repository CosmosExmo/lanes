import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# `discover -s tests` does not import tests/__init__.py, so repeat the path setup
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from lanelib import quota
from lanelib.quota import Window

FIX = Path(__file__).resolve().parent / "fixtures"
IST = ZoneInfo("Europe/Istanbul")


def fx(name):
    return (FIX / name).read_text()


class QuotaCase(unittest.TestCase):
    def test_claude_usage_fixture(self):
        now = datetime(2026, 11, 26, 14, 0, tzinfo=IST)
        ws = {w.name: w for w in quota.parse_claude_usage(fx("claude_usage.txt"), now)}
        self.assertEqual(ws["5h"].used_pct, 13.0)
        self.assertEqual(ws["weekly"].used_pct, 86.0)
        self.assertEqual(ws["weekly:fable"].used_pct, 0.0)
        self.assertEqual(ws["5h"].length_min, 300)
        self.assertEqual(ws["weekly"].length_min, 10080)
        self.assertEqual(ws["weekly"].resets_at,
                         datetime(2026, 11, 27, 8, 0, tzinfo=IST).timestamp())
        self.assertEqual(ws["5h"].resets_at,
                         datetime(2026, 11, 26, 17, 50, tzinfo=IST).timestamp())

    def test_claude_time_only_and_year_rollover(self):
        now = datetime(2026, 12, 31, 20, 0, tzinfo=IST)
        text = ("Current session: 5% used · resets 5:50pm (Europe/Istanbul)\n"
                "Current week (all models): 1% used · resets Jan 2 at 8am (Europe/Istanbul)")
        ws = {w.name: w for w in quota.parse_claude_usage(text, now)}
        # 5:50pm today already passed -> next occurrence is tomorrow
        self.assertEqual(ws["5h"].resets_at,
                         datetime(2027, 1, 1, 17, 50, tzinfo=IST).timestamp())
        self.assertEqual(ws["weekly"].resets_at,
                         datetime(2027, 1, 2, 8, 0, tzinfo=IST).timestamp())

    def test_claude_usage_unknown_format_returns_empty(self):
        now = datetime(2026, 11, 26, 14, 0, tzinfo=IST)
        self.assertEqual(quota.parse_claude_usage("Usage unavailable", now), [])

    def test_codex_parse(self):
        ws, overage = quota.parse_codex_ratelimits(json.loads(fx("codex_ratelimits.json")))
        self.assertEqual(len(ws), 1)
        self.assertEqual((ws[0].name, ws[0].used_pct, ws[0].resets_at, ws[0].length_min),
                         ("weekly", 39.0, 1795943818.0, 10080))
        self.assertTrue(overage)

    def test_codex_null_secondary_skipped(self):
        d = json.loads(fx("codex_ratelimits.json"))
        d["rateLimits"]["credits"]["unlimited"] = True
        ws, overage = quota.parse_codex_ratelimits(d)
        self.assertEqual([w.name for w in ws], ["weekly"])
        self.assertFalse(overage)

    def test_agy_two_pools(self):
        pools = quota.parse_agy_usage(json.loads(fx("agy_usage.json")))
        self.assertEqual(sorted(pools), ["google:claude-gpt", "google:gemini"])
        g = {w.name: w for w in pools["google:gemini"]}
        self.assertAlmostEqual(g["5h"].used_pct, 70.0)
        self.assertAlmostEqual(g["weekly"].used_pct, 19.0)
        self.assertEqual(g["5h"].length_min, 300)
        self.assertEqual(g["5h"].resets_at,
                         datetime(2026, 11, 26, 12, 36, 52, tzinfo=ZoneInfo("UTC")).timestamp())

    def test_cursor_monthly(self):
        ws = quota.parse_cursor_usage(json.loads(fx("cursor_usage.json")))
        self.assertEqual(len(ws), 1)
        self.assertEqual((ws[0].name, ws[0].used_pct, ws[0].resets_at, ws[0].length_min),
                         ("monthly", 1.0, 1798199702.115, 43200))

    def test_window_name(self):
        self.assertEqual([quota.window_name(m) for m in (300, 10080, 40000, 46000, 60)],
                         ["5h", "weekly", "monthly", "monthly", "60m"])

    def test_window_roundtrip(self):
        w = Window("5h", 13.0, 100.0, 300)
        self.assertEqual(Window.from_dict(w.to_dict()), w)

    def test_pace_guards(self):
        now = 1_000_000.0
        week = 10080 * 60
        past = Window("weekly", 50.0, now - 1, 10080)
        self.assertEqual(quota.effective_used(past, now), 0)
        fresh = Window("weekly", 5.0, now + week * 0.995, 10080)
        self.assertEqual(quota.pace(fresh, now), 0.0)
        w = Window("weekly", 86.0, now + week * 0.11, 10080)
        self.assertAlmostEqual(quota.elapsed_pct(w, now), 89.0)
        self.assertTrue(0.96 < quota.pace(w, now) < 0.98)
        self.assertEqual(quota.elapsed_pct(Window("x", 1, now + 10 * week, 10080), now), 0.0)


if __name__ == "__main__":
    unittest.main()
