import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from lanelib import discover, quota, runner, state
from lanelib.discover import Pool

ROOT = Path(__file__).resolve().parent.parent
STUBS = ROOT / "tests" / "stubs"
ADAPTERS = json.loads((ROOT / "adapters.json").read_text())
NOW = time.time()
KIMI = {"pool": "kimi", "cli": "kimi", "provider": None, "model": "k3", "effort": ""}
CODEX = {"pool": "chatgpt", "cli": "codex", "provider": None, "model": "gpt-6-luna", "effort": "low"}
META = {"tier": "T0", "kind": "read", "task": "unit test"}


def P(pid, cli, billing="subscription", status="usable", windows=(), until=0.0):
    return Pool(pid, cli, status, billing, False, list(windows), ["m"], "", NOW, [], until)


class RunnerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env = {"HOME": str(self.tmp / "home"), "LANES_CACHE": str(self.tmp / "cache"),
               "LANES_CONFIG": str(self.tmp / "cfg.json"), "STUB_MODE": "nowrite",
               "PATH": f"{STUBS}{os.pathsep}{os.environ['PATH']}"}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("LANES_DEPTH", None)
        poll = mock.patch.object(runner, "POLL", 0.05)
        poll.start()
        self.addCleanup(poll.stop)
        self.inv(P("kimi", "kimi"))

    def inv(self, *pools):
        state.write_inventory({"version": 1, "pools": {p.id: p.to_dict() for p in pools}})

    def prompt(self, text="do the thing"):
        p = self.tmp / "prompt.in"
        p.write_text(text)
        return p

    def repo(self):
        d = self.tmp / "repo"
        d.mkdir()
        git = lambda *a: subprocess.run(["git", "-C", str(d), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                                         *a], check=True, capture_output=True)
        git("init", "-q")
        (d / "a.txt").write_text("a")
        git("add", ".")
        git("commit", "-qm", "init")
        return d

    def start(self, lane=KIMI, mode="read", cwd=None, **kw):
        a = dict(cwd=cwd or self.tmp, worktree=False, resume=None, bg=False, allow_spend=False,
                 timeout_s=60, meta=META)
        a.update(kw)
        return runner.start_job(lane, mode, self.prompt(), **a)

    def die(self, code, **kw):
        with mock.patch("sys.stderr", io.StringIO()) as err:
            with self.assertRaises(SystemExit) as cm:
                self.start(**kw)
        self.assertEqual(cm.exception.code, code)
        return err.getvalue()

    def meta(self, jid):
        return json.loads((state.CACHE_DIR() / "jobs" / jid / "meta.json").read_text())

    def row(self, jid):
        return next(r for r in state.read_ledger() if r["job"] == jid)

    def git_out(self, d, *a):
        return subprocess.run(["git", "-C", str(d), *a], check=True, capture_output=True, text=True).stdout


class EnvArgvTests(RunnerCase):
    def test_child_env_strips_api_keys(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "x", "ANTHROPIC_API_KEY": "y", "KIMI_API_KEY": "z"}):
            for cli in ("codex", "claude", "kimi"):
                env = runner.child_env(ADAPTERS[cli], "read")
                self.assertFalse({"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "KIMI_API_KEY"} & set(env))
                self.assertEqual(env["LANES_DEPTH"], "1")

    def test_router_argv_is_fully_qualified_and_guarded(self):
        pf, out = self.prompt(), self.tmp / "out"
        pi = {"pool": "chatgpt", "cli": "pi", "provider": "openai-codex", "model": "gpt-6-luna", "effort": "low"}
        argv, stdin = runner.build_argv(ADAPTERS["pi"], pi, "read", pf, out, 60)
        self.assertIn("openai-codex/gpt-6-luna:low", argv)
        self.assertIn("--no-extensions", argv)
        self.assertEqual(stdin, "/dev/null")
        self.assertEqual(runner.child_env(ADAPTERS["pi"], "read")["PI_SUBAGENT_MAX_DEPTH"], "0")
        oc = {"pool": "chatgpt", "cli": "opencode", "provider": "openai", "model": "gpt-6-luna", "effort": "low"}
        for mode in ("read", "write"):
            argv, _ = runner.build_argv(ADAPTERS["opencode"], oc, mode, pf, out, 60)
            self.assertIn("--standalone", argv)
            self.assertIn("openai/gpt-6-luna#low", argv)
            cfg = json.loads(runner.child_env(ADAPTERS["opencode"], mode)["OPENCODE_CONFIG_CONTENT"])
            self.assertEqual(cfg["permission"]["subagent"], "deny")

    def test_claude_argv_is_guarded_and_reads_prompt_on_stdin(self):
        pf = self.prompt("PROMPT-BODY")
        lane = {"pool": "anthropic", "cli": "claude", "provider": None, "model": "sonnet", "effort": "low"}
        for mode in ("read", "write", "write_shell"):
            argv, stdin = runner.build_argv(ADAPTERS["claude"], lane, mode, pf, self.tmp / "o", 60)
            self.assertEqual(argv[-2:], ["--disallowedTools", "Agent"])
            self.assertEqual(stdin, str(pf))
            self.assertNotIn("PROMPT-BODY", " ".join(argv))

    def test_placeholders_filled_including_prompt_text_and_timeout(self):
        pf = self.prompt("PROMPT-BODY")
        lane = {"pool": "qwen", "cli": "qwen", "provider": None, "model": "q1", "effort": ""}
        argv, _ = runner.build_argv(ADAPTERS["qwen"], lane, "read", pf, self.tmp / "o", 77)
        self.assertIn("77", argv)
        self.assertIn("PROMPT-BODY", argv)
        self.assertFalse([a for a in argv if "{" in a])


class RefusalTests(RunnerCase):
    def test_run_refuses_when_nested(self):
        with mock.patch.dict(os.environ, {"LANES_DEPTH": "1"}):
            self.assertIn("nested delegation refused", self.die(3))

    def test_api_lane_needs_allow_spend(self):
        self.inv(P("api:deepseek", "opencode", billing="api", status="api_billing"))
        lane = {"pool": "api:deepseek", "cli": "opencode", "provider": "deepseek", "model": "m", "effort": "low"}
        self.assertIn("approval needed: api:deepseek is api-billed", self.die(4, lane=lane))
        jid = self.start(lane, allow_spend=True)
        self.assertEqual(self.meta(jid)["status"], "done")

    def test_unavailable_and_unknown_pools(self):
        self.inv(P("kimi", "kimi", status="not_logged_in"))
        self.assertIn("kimi unavailable: not_logged_in", self.die(2))
        self.inv(P("kimi", "kimi", status="exhausted", until=NOW + 3600))
        self.assertIn("kimi unavailable: exhausted", self.die(2))
        self.inv(P("kimi", "kimi", status="exhausted", until=NOW - 5))  # reset has passed
        self.assertEqual(self.meta(self.start())["status"], "done")
        self.inv()
        self.assertIn("run lanes discover first", self.die(2))

    def test_write_job_needs_git_repo(self):
        self.assertIn("write jobs need a git repo", self.die(2, mode="write", worktree=True))

    def test_bad_job_ids_are_rejected(self):
        with mock.patch("sys.stderr", io.StringIO()):
            for f in (lambda: runner.wait_jobs(["../x"], 1), lambda: runner.mark("a/b", "ok"),
                      lambda: runner.exec_job("..")):
                with self.assertRaises(SystemExit):
                    f()


class WorktreeTests(RunnerCase):
    def test_write_job_in_worktree_creates_branch(self):
        os.environ["STUB_MODE"] = "write"
        repo = self.repo()
        jid = self.start(mode="write", cwd=repo, worktree=True)
        wt = state.CACHE_DIR() / "worktrees" / jid
        self.assertEqual((wt / "stub_output.txt").read_text(), "changed")
        self.assertFalse((repo / "stub_output.txt").exists())
        self.assertIn(f"lanes/{jid}", self.git_out(repo, "branch", "--list"))
        m = self.meta(jid)
        self.assertEqual((m["status"], m["worktree"]), ("done", str(wt)))
        self.assertEqual(self.row(jid)["worktree"], str(wt))

    def test_write_job_without_diff_is_no_changes(self):
        os.environ["STUB_MODE"] = "nowrite"
        jid = self.start(mode="write", cwd=self.repo(), worktree=True)
        self.assertEqual(self.meta(jid)["status"], "no_changes")
        self.assertEqual(self.row(jid)["status"], "no_changes")

    def test_resume_reuses_worktree(self):
        os.environ["STUB_MODE"] = "write"
        repo = self.repo()
        first = self.start(mode="write", cwd=repo, worktree=True)
        os.environ["STUB_MODE"] = "nowrite"
        second = self.start(mode="write", cwd=repo, worktree=True, resume=first)
        self.assertNotEqual(first, second)
        self.assertEqual(self.meta(second)["worktree"], self.meta(first)["worktree"])
        self.assertEqual(self.meta(second)["status"], "no_changes")  # earlier edits are not new changes
        self.assertEqual(len(list((state.CACHE_DIR() / "worktrees").iterdir())), 1)

    def test_resume_of_unknown_worktree_fails(self):
        self.assertIn("unknown worktree", self.die(2, mode="write", worktree=True, resume="nope"))


class ExecTests(RunnerCase):
    def test_limit_error_marks_pool_exhausted(self):
        os.environ["STUB_MODE"] = "limit"
        soon = NOW + 1800
        self.inv(P("chatgpt", "codex", windows=[quota.Window("weekly", 10, NOW + 86400, 10080),
                                                quota.Window("5h", 10, soon, 300)]))
        with mock.patch.object(discover, "read_pool_windows", return_value=None):
            jid = self.start(CODEX)
        self.assertEqual(self.meta(jid)["status"], "limit")
        p = state.read_inventory(None)["pools"]["chatgpt"]
        self.assertEqual((p["status"], p["exhausted_until"]), ("exhausted", soon))
        self.assertIn("chatgpt unavailable", self.die(2, lane=CODEX))

    def test_limit_without_windows_blocks_for_an_hour(self):
        os.environ["STUB_MODE"] = "limit"
        self.start()
        p = state.read_inventory(None)["pools"]["kimi"]
        self.assertAlmostEqual(p["exhausted_until"], time.time() + 3600, delta=30)

    def test_other_failures_are_failed_not_limit(self):
        os.environ["STUB_MODE"] = "broken"  # model_call: "something broke", exit 1
        jid = self.start()
        self.assertEqual((self.meta(jid)["status"], self.meta(jid)["rc"]), ("failed", 1))
        self.assertEqual(state.read_inventory(None)["pools"]["kimi"]["status"], "usable")

    def test_classify_failure(self):
        a = ADAPTERS["codex"]
        self.assertIsNone(runner.classify_failure(a, 0, "usage_limit_exceeded in the answer", ""))
        self.assertEqual(runner.classify_failure(a, 1, "", "Error: Usage_Limit_Exceeded"), "limit")
        self.assertEqual(runner.classify_failure(a, 1, "", "401 Unauthorized"), "auth")
        self.assertEqual(runner.classify_failure(a, 124, "", "timeout"), "error")

    def test_bg_then_wait_returns_done(self):
        jid = self.start(bg=True)
        res = runner.wait_jobs([jid], 60)
        self.assertEqual([(r["job"], r["status"]) for r in res], [(jid, "done")])
        self.assertEqual(self.row(jid)["status"], "done")

    def test_wait_times_out_and_reaps_dead_jobs(self):
        jid = self.start()
        m = self.meta(jid)
        m.update(status="running", pid=2 ** 22 + 12345)  # no such process
        (state.CACHE_DIR() / "jobs" / jid / "meta.json").write_text(json.dumps(m))
        self.assertEqual(runner.wait_jobs([jid], 5)[0]["status"], "failed")
        m.update(status="running", pid=os.getpid())  # alive and never finishing
        (state.CACHE_DIR() / "jobs" / jid / "meta.json").write_text(json.dumps(m))
        t = time.time()
        self.assertEqual(runner.wait_jobs([jid], 0.2)[0]["status"], "running")
        self.assertLess(time.time() - t, 3)
        self.assertEqual(runner.wait_jobs(["20200101-000000-dead"], 1)[0]["status"], "unknown")

    def test_timeout_kills_the_job(self):
        slow = self.tmp / "bin"
        slow.mkdir()
        (slow / "kimi").write_text("#!/bin/sh\nsleep 30\n")
        (slow / "kimi").chmod(0o755)
        os.environ["PATH"] = f"{slow}{os.pathsep}{os.environ['PATH']}"
        t = time.time()
        jid = self.start(timeout_s=1)
        self.assertLess(time.time() - t, 15)
        self.assertEqual((self.meta(jid)["status"], self.meta(jid)["rc"]), ("failed", 124))

    def test_answer_extraction(self):
        os.environ["STUB_MODE"] = "json"
        out = lambda j: (state.CACHE_DIR() / "jobs" / j / "out.txt").read_text()
        claude = {"pool": "anthropic", "cli": "claude", "provider": None, "model": "sonnet", "effort": "low"}
        self.inv(P("anthropic", "claude"), P("chatgpt", "codex"), P("kimi", "kimi"))
        with mock.patch.object(discover, "read_pool_windows", return_value=None):
            self.assertEqual(out(self.start(claude)), "ANSWER")  # answer_field "result"
            self.assertEqual(out(self.start(CODEX)), "ANSWER")  # {out} file written by the CLI
            self.assertIn('"result"', out(self.start()))  # no answer_field: raw stdout
        jid = self.start()
        d = state.CACHE_DIR() / "jobs" / jid
        for n in ("prompt.txt", "argv.json", "meta.json", "stdout.txt", "stderr.txt", "out.txt"):
            self.assertEqual((d / n).stat().st_mode & 0o777, 0o600, n)
        self.assertTrue((d / "prompt.txt").read_text().startswith(runner.PREAMBLE))
        self.assertIn("do the thing", (d / "prompt.txt").read_text())


class LedgerTests(RunnerCase):
    def test_ledger_row_has_no_prompt_text_or_email(self):
        os.environ["STUB_MODE"] = "json"
        pf = self.tmp / "p.txt"
        pf.write_text("SECRET-PROMPT-TEXT mail me at boss@example.com")
        jid = runner.start_job(KIMI, "read", pf, cwd=self.tmp, worktree=False, resume=None, bg=False,
                               allow_spend=False, timeout_s=60,
                               meta={"tier": "T1", "kind": "review", "task": "ask boss@example.com"})
        raw = (state.CACHE_DIR() / "ledger.jsonl").read_text()
        for bad in ("SECRET-PROMPT-TEXT", "boss@example.com", "ANSWER"):
            self.assertNotIn(bad, raw)
        r = self.row(jid)
        for k, v in dict(pool="kimi", cli="kimi", model="k3", tier="T1", kind="review", mode="read",
                         rc=0, status="done", worktree=None, outcome=None).items():
            self.assertEqual(r[k], v, k)
        self.assertIn("<email>", r["task"])
        self.assertNotIn("delta", r)  # kimi has no quota reader: omitted, not None
        self.assertIsInstance(r["duration_s"], float)

    def windows(self, *used):
        it = iter(used)
        return lambda *a, **k: [quota.Window("5h", next(it), NOW + 3600, 300),
                                quota.Window("weekly", 50.0, NOW + 86400, 10080)]

    def test_delta_is_measured_from_quota_before_and_after(self):
        self.inv(P("chatgpt", "codex"))
        with mock.patch.object(discover, "read_pool_windows", side_effect=self.windows(10.0, 12.5)):
            jid = self.start(CODEX)
        d = self.row(jid)["delta"]
        self.assertEqual(d, {"5h": 2.5, "weekly": 0.0})
        self.assertTrue(all(type(v) is float for v in d.values()))

    def test_delta_omitted_when_jobs_overlap_or_quota_unreadable(self):
        self.inv(P("chatgpt", "codex"))
        other = state.CACHE_DIR() / "jobs" / "20200101-000000-aaaa"
        other.mkdir(parents=True)
        (other / "meta.json").write_text(json.dumps({"job": "x", "pool": "chatgpt", "status": "running",
                                                      "started": NOW - 5, "pid": os.getpid()}))
        with mock.patch.object(discover, "read_pool_windows", side_effect=self.windows(10.0, 12.5)):
            self.assertNotIn("delta", self.row(self.start(CODEX)))
        (other / "meta.json").write_text(json.dumps({"job": "x", "pool": "chatgpt", "status": "done",
                                                      "started": NOW - 500, "finished": NOW - 400}))
        with mock.patch.object(discover, "read_pool_windows", return_value=None):
            self.assertNotIn("delta", self.row(self.start(CODEX)))
        with mock.patch.object(discover, "read_pool_windows", side_effect=self.windows(10.0, 11.0)):
            self.assertEqual(self.row(self.start(CODEX))["delta"]["5h"], 1.0)  # finished job did not overlap

    def test_reset_window_gives_no_negative_delta(self):
        self.inv(P("chatgpt", "codex"))
        with mock.patch.object(discover, "read_pool_windows", side_effect=self.windows(90.0, 3.0)):
            self.assertNotIn("5h", self.row(self.start(CODEX)).get("delta", {}))

    def test_mark_sets_outcome_and_ledger_reads_back(self):
        jid = self.start()
        runner.mark(jid, "accepted")
        self.assertEqual(self.row(jid)["outcome"], "accepted")
        self.assertEqual([r["job"] for r in runner.ledger()], [jid])
        self.assertEqual(runner.ledger(since=time.time() + 100), [])
        with mock.patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit):
                runner.mark("20200101-000000-dead", "accepted")


if __name__ == "__main__":
    unittest.main()
