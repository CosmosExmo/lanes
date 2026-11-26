import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STUBS = ROOT / "tests" / "stubs"
sys.path.insert(0, str(ROOT / "scripts"))
from lanelib import state  # noqa: E402
from lanelib.pick import Candidate  # noqa: E402

NOW = time.time()
POOL = {"id": "kimi", "cli": "kimi", "status": "usable", "billing": "subscription", "overage": False,
        "windows": [{"name": "weekly", "length_min": 10080, "used_pct": 10.0, "resets_at": NOW + 86400}],
        "models": ["kimi-for-coding-highspeed"], "note": "", "checked_at": NOW, "access": [{"cli": "kimi", "provider": None, "models": ["kimi-for-coding-highspeed"]}],
        "exhausted_until": 0.0}


class CliCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.env = {"HOME": str(self.home), "LANES_CACHE": str(self.tmp / "cache"),
                    "LANES_CONFIG": str(self.tmp / "cfg.json"), "STUB_MODE": "ok",
                    "PATH": f"{STUBS}{os.pathsep}{os.environ['PATH']}"}
        os.environ.update({k: self.env[k] for k in ("LANES_CACHE", "LANES_CONFIG")})
        self.addCleanup(lambda: [os.environ.pop(k, None) for k in ("LANES_CACHE", "LANES_CONFIG")])

    def run_cli(self, *args, **env):
        e = {**self.env, **env}
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "lanes.py"), *args],
                              capture_output=True, text=True, env=e, cwd=self.tmp)

    def inv(self):
        state.write_inventory({"version": 1, "checked_at": NOW, "quota_at": NOW, "models_at": NOW,
                               "pools": {"kimi": POOL}})


class TestCli(CliCase):
    def test_pick_json_shape(self):
        self.inv()
        r = self.run_cli("pick", "--tier", "T0", "--kind", "read", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = json.loads(r.stdout)
        self.assertTrue(rows)
        self.assertEqual(set(rows[0]), set(Candidate.__dataclass_fields__))
        self.assertEqual(rows[0]["pool"], "kimi")

    def test_pick_text(self):
        self.inv()
        r = self.run_cli("pick", "--tier", "T0", "--kind", "read")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("kimi "))

    def test_selftest_flags_missing(self):
        r = self.run_cli("selftest", STUB_MODE="nohelp")
        self.assertEqual(r.returncode, 5, r.stdout + r.stderr)
        self.assertIn("flags_missing", r.stdout)

    def test_selftest_ok(self):
        r = self.run_cli("selftest", "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rows = {x["cli"]: x for x in json.loads(r.stdout)}
        self.assertEqual(rows["claude"]["status"], "ok")
        self.assertEqual(set(rows["claude"]), {"cli", "installed", "version", "verified_version",
                                               "flags_missing", "live_ok", "status"})

    def test_install_skips_real_dir(self):
        real = self.home / ".claude" / "skills" / "token-efficient-workflow"
        real.mkdir(parents=True)
        (real / "keep").write_text("x")
        (self.home / ".agents" / "skills").mkdir(parents=True)
        r = self.run_cli("install")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((real / "keep").is_file() and not real.is_symlink())
        link = self.home / ".agents" / "skills" / "token-efficient-workflow"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), ROOT)
        self.assertEqual((self.home / ".local" / "bin" / "lanes").resolve(), ROOT / "scripts" / "lanes.py")

    def test_install_dry_run(self):
        r = self.run_cli("install", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.home / ".local").exists())

    def test_ledger_since_filter(self):
        state.append_ledger({"job": "old", "pool": "kimi", "outcome": "used"})
        rows = state.read_ledger()
        rows[0]["ts"] = NOW - 10 * 86400
        (state.CACHE_DIR() / "ledger.jsonl").write_text(json.dumps(rows[0]) + "\n")
        state.append_ledger({"job": "new", "pool": "kimi", "outcome": "wasted"})
        r = self.run_cli("ledger", "--since", "7d", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["jobs"], 1)
        self.assertEqual(out["by_outcome"], {"wasted": 1})
        self.assertEqual(json.loads(self.run_cli("ledger", "--json").stdout)["jobs"], 2)

    def test_run_refuses_nested_and_self(self):
        self.inv()
        pf = self.tmp / "p.txt"
        pf.write_text("hi")
        base = ["run", "--pool", "kimi", "--cli", "kimi", "--model", "k3", "--effort", "low",
                "--mode", "read", "--prompt-file", str(pf)]
        self.assertEqual(self.run_cli(*base, LANES_DEPTH="1").returncode, 3)
        r = self.run_cli(*base, CLAUDECODE="1")
        self.assertEqual(r.returncode, 0, r.stderr)  # self pool is anthropic, not kimi
        r = self.run_cli(*[a if a != "kimi" else "anthropic" for a in base], CLAUDECODE="1")
        self.assertEqual(r.returncode, 2)
        self.assertIn("own pool", r.stderr)

    def test_mark_rejects_bad_outcome(self):
        self.assertEqual(self.run_cli("mark", "x", "bogus").returncode, 2)


if __name__ == "__main__":
    unittest.main()
