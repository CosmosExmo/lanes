import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from lanelib import pick
from lanelib.discover import Pool
from lanelib.pick import Request

ROOT = Path(__file__).resolve().parent.parent
ADAPTERS = json.loads((ROOT / "adapters.json").read_text())
CAPS = {"models": [
    {"pattern": "gpt-6-luna", "pool": "chatgpt", "tiers": ["T0", "T1"], "strengths": []},
    {"pattern": "sonnet", "pool": "anthropic", "cli": "claude", "tiers": ["T0", "T1", "T2"], "strengths": ["code"]},
    {"pattern": "gemini-4-*", "pool": "google:gemini", "cli": "agy", "tiers": ["T0", "T1"], "strengths": []},
    {"pattern": "composer-2.5*", "pool": "cursor", "cli": "cursor", "tiers": ["T0", "T1"], "strengths": []},
]}
CFG = {"orchestrator_reserve_pct": 3, "agy_allow_shell": False}
NOW = 1_800_000_000.0
H = 3600.0
WEEK = 10080


def W(used, resets_in_h, length=WEEK, name="weekly"):
    return pick.Window(name, used, NOW + resets_in_h * H, length)


def pool(pid="chatgpt", windows=(), cli="codex", models=("gpt-6-luna",), status="usable",
         billing="subscription", overage=False, provider=None):
    return Pool(pid, cli, status, billing, overage, list(windows), list(models), "", NOW,
                [{"cli": cli, "provider": provider, "models": list(models)}])


def rank(pools, req=None, ledger=(), cfg=CFG):
    req = req or Request("T0", "read", "x")
    return pick.rank({p.id: p for p in pools}, CAPS, ADAPTERS, cfg, req, list(ledger), NOW)


class PickCase(unittest.TestCase):
    def test_soon_reset_pool_first(self):
        a = pool("chatgpt", [W(60, 1)])
        b = pool("anthropic", [W(40, 6 * 24)], cli="claude", models=("sonnet",))
        self.assertEqual([c.pool for c in rank([b, a])], ["chatgpt", "anthropic"])

    def test_tail_takes_only_small_jobs(self):
        p = pool(windows=[W(98, 72)])
        self.assertEqual(len(rank([p], Request("T0", "read", "x"))), 1)
        self.assertEqual(rank([p], Request("T1", "write", "x")), [])

    def test_weekly_brake_on_long_jobs(self):
        p = pool(windows=[W(65, WEEK / 60 / 2)])  # half elapsed, 65% used -> pace 1.3
        self.assertTrue(rank([p], Request("T1", "write", "x"))[0].brake)
        self.assertFalse(rank([p], Request("T0", "read", "x"))[0].brake)

    def test_review_excludes_author_pool(self):
        ps = [pool("chatgpt", [W(10, 100)]), pool("anthropic", [W(10, 100)], cli="claude", models=("sonnet",))]
        got = rank(ps, Request("T1", "review", "x", author_pool="chatgpt"))
        self.assertEqual([c.pool for c in got], ["anthropic"])

    def test_own_pool_is_native_with_reserve(self):
        req = Request("T0", "read", "x", from_pool="chatgpt")
        c = rank([pool(windows=[W(50, WEEK / 60 / 2)])], req)[0]
        self.assertEqual((c.cli, c.provider), ("native", None))
        # 98.5% used: 1.5 left fits for a foreign caller, but native keeps 1.5% (3% * half window)
        tail = pool(windows=[W(98.5, WEEK / 60 / 2)])
        self.assertEqual(len(rank([tail], Request("T0", "read", "x"))), 1)
        self.assertEqual(rank([tail], req), [])

    def test_api_billing_needs_approval(self):
        free = pool("chatgpt", [W(90, 100)])
        api = pool("anthropic", [], cli="claude", models=("sonnet",), status="api_billing", billing="api")
        got = rank([api, free])
        self.assertEqual([(c.pool, c.needs_approval) for c in got], [("chatgpt", False), ("anthropic", True)])

    def test_unverified_subscription_is_not_a_candidate(self):
        p = pool(status="logged_in_unverified", billing="subscription")
        self.assertEqual(rank([p]), [])
        p = pool(status="logged_in_unverified", billing="unknown")
        self.assertTrue(rank([p])[0].needs_approval)

    def test_overage_pool_never_listed_when_cost_exceeds_remaining(self):
        req = Request("T1", "write", "x")
        self.assertEqual(rank([pool(windows=[W(99.9, 72)], overage=True)], req), [])
        self.assertFalse(rank([pool(windows=[W(50, 72)], overage=True)], req)[0].needs_approval)

    def test_needs_shell_skips_agy(self):
        p = pool("google:gemini", [W(10, 72)], cli="agy", models=("gemini-4-pro",))
        req = Request("T1", "write", "x", needs_shell=True)
        self.assertEqual(rank([p], req), [])
        self.assertEqual(rank([p], req, cfg={**CFG, "agy_allow_shell": True})[0].cli, "agy")
        self.assertEqual(len(rank([p], Request("T1", "write", "x"))), 1)

    def test_quota_unknown_ranks_after_known_headroom(self):
        unk = pool("anthropic", [], cli="claude", models=("sonnet",), status="quota_unknown")
        known = pool("chatgpt", [W(50, 72)])
        self.assertEqual([c.pool for c in rank([unk, known])], ["chatgpt", "anthropic"])
        self.assertEqual(rank([unk])[0].urgency, 0)

    def test_measured_cost_overrides_default(self):
        row = {"pool": "chatgpt", "tier": "T0", "kind": "read", "delta": {"weekly": 10}}
        self.assertEqual(pick.estimate_cost("chatgpt", "T0", "read", "weekly", [row] * 3), 10)
        self.assertEqual(pick.estimate_cost("chatgpt", "T0", "read", "weekly", [row] * 2), 0.5)
        got = rank([pool(windows=[W(50, 72)])], ledger=[row] * 3)
        self.assertEqual(got[0].est_cost, {"weekly": 10})

    def test_size_class_and_unknown_window_default(self):
        self.assertEqual([pick.size_class(*a) for a in
                          [("T0", "write"), ("T0", "review"), ("T1", "review"), ("T1", "read"),
                           ("T2", "read"), ("T2", "review"), ("T1", "write")]],
                         ["S", "S", "S", "M", "M", "M", "L"])
        self.assertEqual(pick.estimate_cost("p", "T1", "write", "300m", []), 4)

    def test_model_suffix_effort_and_candidate_dict(self):
        p = pool("cursor", [W(10, 72)], cli="cursor", models=("composer-2.5", "composer-2.5-medium"))
        c = rank([p], Request("T1", "read", "x"))[0]
        self.assertEqual((c.model, c.effort), ("composer-2.5-medium", "medium"))
        json.dumps(c.to_dict())

    def test_model_scoped_window_only_gates_that_model(self):
        p = pool("anthropic", [W(10, 72), W(100, 72, name="weekly:fable")], cli="claude", models=("sonnet",))
        self.assertEqual(len(rank([p])), 1)

    def test_n_limits_results(self):
        ps = [pool("chatgpt", [W(10, 72)]), pool("anthropic", [W(10, 72)], cli="claude", models=("sonnet",))]
        self.assertEqual(len(rank(ps, Request("T0", "read", "x", n=1))), 1)


if __name__ == "__main__":
    unittest.main()
