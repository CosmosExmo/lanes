import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

# `discover -s tests` does not import tests/__init__.py, so repeat the path setup
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from lanelib import state


class StateCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._saved = {k: os.environ.get(k) for k in ("LANES_CACHE", "LANES_CONFIG")}
        self.addCleanup(self._restore)
        os.environ["LANES_CACHE"] = os.path.join(self.tmp.name, "cache")
        os.environ["LANES_CONFIG"] = os.path.join(self.tmp.name, "config.json")

    def _restore(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_inventory_roundtrip_and_ttl(self):
        state.write_inventory({"pools": {}})
        self.assertEqual(state.read_inventory(60), {"pools": {}})
        p = state.CACHE_DIR() / "inventory.json"
        old = time.time() - 120
        os.utime(p, (old, old))
        self.assertIsNone(state.read_inventory(60))
        self.assertEqual(state.read_inventory(None), {"pools": {}})

    def test_ledger_parallel_appends_are_valid_jsonl(self):
        def work(t):
            for i in range(50):
                state.append_ledger({"job": t * 50 + i})
        ts = [threading.Thread(target=work, args=(t,)) for t in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        lines = (state.CACHE_DIR() / "ledger.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), 400)
        for l in lines:
            json.loads(l)
        self.assertEqual(len(state.read_ledger()), 400)

    def test_update_ledger_sets_outcome(self):
        state.append_ledger({"job": "a"})
        state.update_ledger("a", outcome="used")
        rows = [r for r in state.read_ledger() if r["job"] == "a"]
        self.assertEqual(rows[-1]["outcome"], "used")

    def test_redact(self):
        out = state.redact("x a.b@c.io Bearer abc.def sk-123 eyJa.eyJb.c")
        for s in ("a.b@c.io", "abc.def", "sk-123", "eyJa.eyJb.c"):
            self.assertNotIn(s, out)

    def test_load_config_defaults(self):
        self.assertEqual(state.load_config(), {
            "cursor_read_quota": False, "agy_allow_shell": False,
            "orchestrator_reserve_pct": 3})

    def test_load_config_merges_file(self):
        with open(os.environ["LANES_CONFIG"], "w") as f:
            json.dump({"agy_allow_shell": True}, f)
        c = state.load_config()
        self.assertTrue(c["agy_allow_shell"])
        self.assertEqual(c["orchestrator_reserve_pct"], 3)

    def test_job_ids(self):
        jid = state.new_job_id()
        self.assertRegex(jid, r"^\d{8}-\d{6}-[0-9a-f]{4}$")
        self.assertTrue(state.job_dir(jid).is_dir())


if __name__ == "__main__":
    unittest.main()
