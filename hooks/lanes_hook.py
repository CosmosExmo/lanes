#!/usr/bin/env python3
"""lanes hooks.

    lanes_hook.py hint    UserPromptSubmit: point the agent at the lanes skill on matching prompts
    lanes_hook.py guard   PreToolUse (Claude Code Agent/Task): refuse a spawn without model + effort

Reads the hook payload on stdin: Claude Code's own JSON, or a neutral envelope with the same
`prompt` / `tool_name` / `tool_input` keys plus `harness`. Exit 0 = continue (stdout becomes
context); exit 2 = block, reason on stderr. Any internal error exits 0 so a broken hook never
stops the session. Switches: `prompt_hint` and `agent_guard` (both default off) in the
lanes config file.
"""
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

# Matched against the lowercased prompt with apostrophes removed. Compound phrases only, so
# "rate limit", SQL LIMIT or "the cheapest option" stay quiet.
TRIGGERS = re.compile("|".join([
    r"token[- ]?ef+i", r"\bef+ici?e?nt(ly)? work", r"\bsav(e|ing) tokens", r"token (budget|saving)",
    r"\b(weekly|5h) (is |at )*%?\d",
    r"\b(usage|weekly|subscription|plan|5h|5-hour|session) (limit|quota)",
    r"\b(limit|quota) (is )?(almost |nearly )?(full|filling|running out|exhausted)",
    r"second opinion", r"cross[- ](provider |model )?review",
    r"review(ed)? (it )?(by|with|from) (another|a different|other) (provider|model|agent)",
    r"\b(another|a different) (model|provider) (review|look|check)",
    r"\bask (codex|gemini|claude|cursor|agy|kimi|qwen)\b",
    r"\b(spread|distribute|split) (the )?(load|work)", r"\bparallel (agents|sessions|subagents|workers)",
    r"\bcheap(er|ly)? (model|lane|agent)", r"\bsub-?agents?\b",
    r"\b(use|with|via|run|install|update) lanes\b", r"\blanes (install|update|run|pick)",
]))

HINT = ("lanes: the user asked for token-efficient, quota-aware or cross-provider work. Invoke the `lanes` skill as "
        "your first action, before any other tool call, even if the task looks small (Skill tool in Claude Code; "
        "elsewhere read its SKILL.md). The skill itself decides whether to work inline, spawn a subagent or use "
        "another subscription; you report which in one line.")

GUARD = ("lanes agent guard: this {tool} call has no explicit {missing}. An omitted model inherits the "
         "session's own (most expensive) model, and an omitted effort falls back to that model's default. "
         "Re-issue the call with both, chosen by task shape: lookup, grep or test run -> model haiku, "
         "effort low; one traced flow or a scoped edit -> sonnet, medium; judging one hypothesis or one "
         "diff -> one tier below the session model (sonnet from Opus), high. Synthesis stays in the main "
         "session. Forks are exempt. To turn this guard off, set \"agent_guard\": false in the lanes config "
         "($LANES_CONFIG or ~/.config/lanes/config.json).")


def normalize(text):
    return re.sub(r"['\u2019`]", "", text.lower())


def hint(payload):
    if os.environ.get("LANES_DEPTH"):
        return 0  # inside a lane: no delegation, so no hint
    prompt = payload.get("prompt")
    if isinstance(prompt, str) and TRIGGERS.search(normalize(prompt)):
        print(HINT)
    return 0


def guard(payload):
    if payload.get("harness") not in (None, "claude"):
        return 0  # only Claude Code's Agent tool takes model + effort
    if payload.get("tool_name") not in ("Agent", "Task"):
        return 0
    ti = payload.get("tool_input")
    if not isinstance(ti, dict) or ti.get("subagent_type") == "fork":
        return 0
    missing = [k for k in ("model", "effort") if not ti.get(k)]
    if not missing:
        return 0
    print(GUARD.format(tool=payload["tool_name"], missing=" or ".join(missing)), file=sys.stderr)
    return 2


def main(argv):
    mode = argv[1] if len(argv) > 1 else ""
    if mode not in ("hint", "guard"):
        print("usage: lanes_hook.py hint|guard", file=sys.stderr)
        return 0
    from lanelib import state  # inside main: an import failure exits 0 like any other error
    if not state.load_config().get("prompt_hint" if mode == "hint" else "agent_guard"):
        return 0
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    if not isinstance(payload, dict):
        return 0
    return hint(payload) if mode == "hint" else guard(payload)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except SystemExit:
        raise
    except Exception:  # a broken hook must never stop the session
        sys.exit(0)
