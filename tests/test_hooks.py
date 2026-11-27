import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "lanes_hook.py"

SHOULD_HINT = [
    "token efficent çalış", "Token verimli çalış lütfen", "İkinci görüş alalım", "Bunu Codex'e sor",
    "Gemini’ye sorsana", "Claude limitim doluyor", "haftalık limit %84 oldu", "işi dağıt",
    "get a second opinion on this diff", "my weekly limit is almost full", "weekly is at 84%",
    "use lanes for this", "cross-review the change", "ask codex what it thinks", "paralel ajan kullan",
    "kotam doluyor", "bu diffi başka bir modele review ettir", "lanes kur", "spin up three subagents",
]
SHOULD_NOT_HINT = [
    "add a rate limit to the API", "SELECT * FROM t LIMIT 10", "bu ürün ucuz mu",
    "kota alanını formda göster", "fix the login bug", "token refresh is broken",
    "Claude Code'da yeni bir proje aç", "the cheapest option is fine", "add highway lanes to the map",
]
CLAUDE_AGENT = {"session_id": "s", "hook_event_name": "PreToolUse", "tool_name": "Agent",
                "tool_input": {"description": "d", "prompt": "p", "subagent_type": "general-purpose",
                               "model": "haiku", "effort": "low"}}


class HookCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cfg = self.tmp / "cfg.json"

    def run_hook(self, mode, payload, cfg=None, **env):
        if cfg is not None:
            self.cfg.write_text(json.dumps(cfg))
        e = {k: v for k, v in os.environ.items() if k != "LANES_DEPTH"}
        e.update(LANES_CONFIG=str(self.cfg), **env)
        data = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.run([sys.executable, str(HOOK), mode], input=data, capture_output=True,
                              text=True, env=e)

    def test_hint_fires_on_trigger_phrases(self):
        for prompt in SHOULD_HINT:
            with self.subTest(prompt=prompt):
                r = self.run_hook("hint", {"prompt": prompt})
                self.assertEqual(r.returncode, 0)
                self.assertIn("`lanes` skill", r.stdout)

    def test_hint_stays_quiet_on_ordinary_prompts(self):
        for prompt in SHOULD_NOT_HINT:
            with self.subTest(prompt=prompt):
                r = self.run_hook("hint", {"prompt": prompt})
                self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_hint_reads_the_neutral_envelope(self):
        r = self.run_hook("hint", {"harness": "codex", "event": "UserPromptSubmit", "prompt": "ikinci görüş"})
        self.assertIn("lanes", r.stdout)

    def test_hint_off_inside_a_lane_or_by_config(self):
        self.assertEqual(self.run_hook("hint", {"prompt": "second opinion"}, LANES_DEPTH="1").stdout, "")
        self.assertEqual(self.run_hook("hint", {"prompt": "second opinion"}, {"prompt_hint": False}).stdout, "")

    def test_guard_is_off_by_default(self):
        bare = {**CLAUDE_AGENT, "tool_input": {"prompt": "p"}}
        self.assertEqual(self.run_hook("guard", bare).returncode, 0)

    def test_guard_blocks_a_spawn_without_model_or_effort(self):
        for drop in ("model", "effort"):
            with self.subTest(drop=drop):
                ti = {k: v for k, v in CLAUDE_AGENT["tool_input"].items() if k != drop}
                r = self.run_hook("guard", {**CLAUDE_AGENT, "tool_input": ti}, {"agent_guard": True})
                self.assertEqual(r.returncode, 2)
                self.assertIn(f"no explicit {drop}", r.stderr)
                self.assertIn("haiku", r.stderr)
                self.assertNotIn(str(self.cfg), r.stderr)

    def test_guard_allows_explicit_spawns_forks_and_other_tools(self):
        on = {"agent_guard": True}
        self.assertEqual(self.run_hook("guard", CLAUDE_AGENT, on).returncode, 0)
        fork = {**CLAUDE_AGENT, "tool_input": {"subagent_type": "fork", "prompt": "p"}}
        self.assertEqual(self.run_hook("guard", fork, on).returncode, 0)
        bash = {**CLAUDE_AGENT, "tool_name": "Bash", "tool_input": {"command": "ls"}}
        self.assertEqual(self.run_hook("guard", bash, on).returncode, 0)
        codex = {"harness": "codex", "tool_name": "Agent", "tool_input": {"prompt": "p"}}
        self.assertEqual(self.run_hook("guard", codex, on).returncode, 0)

    def test_bad_input_never_blocks(self):
        for bad in ("", "not json", "[1]", "null"):
            for mode in ("hint", "guard"):
                with self.subTest(bad=bad, mode=mode):
                    r = self.run_hook(mode, bad, {"agent_guard": True})
                    self.assertEqual((r.returncode, r.stdout), (0, ""))


class ManifestCase(unittest.TestCase):
    def frontmatter(self):
        text = (ROOT / "SKILL.md").read_text()
        block = text.split("---\n")[1]
        return dict(re.findall(r"^([a-z_-]+): (.*)$", block, re.M))

    def test_skill_listing_fits_the_limits(self):
        fm = self.frontmatter()
        self.assertEqual(fm["name"], "lanes")
        self.assertLessEqual(len(fm["description"]), 1024)
        self.assertLessEqual(len(fm["description"]) + len(fm.get("when_to_use", "")), 1536)

    def test_plugin_version_matches(self):
        manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
        self.assertEqual(manifest["name"], "lanes")
        self.assertEqual(manifest["version"], (ROOT / "VERSION").read_text().strip())

    def test_plugin_hooks_point_at_the_hook_script(self):
        hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]
        cmds = [h["command"] for ev in hooks.values() for m in ev for h in m["hooks"]]
        self.assertEqual(len(cmds), 2)
        for c in cmds:
            self.assertIn("${CLAUDE_PLUGIN_ROOT}/hooks/lanes_hook.py", c)


if __name__ == "__main__":
    unittest.main()
