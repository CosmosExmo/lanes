# Installing lanes — instructions for the AI agent

You are an AI coding agent installing `lanes` for your user. Follow these steps in order. The user is talking to you,
not typing commands: run the commands yourself, explain what you found in plain language, and **ask before every
change to their machine**. Talk to the user in the language they use with you.

Ground rules:
- Never print or paste secrets, tokens or emails. When you mention an API-key variable, give its name only.
- Nothing in steps 1–4 sends a model prompt. Only `--probe` and `selftest --live` do; run them only after the user agrees
  (each sends one tiny prompt per pool or CLI, which uses a little of that subscription).
- If a step fails, show the error, explain it, and ask how to proceed. Do not work around it silently.
- Commands below use `lanes`. Until it is on `PATH` (step 4), run `python3 <repo>/scripts/lanes.py` instead.

## 1. Check prerequisites

```
git --version
python3 --version          # needs 3.9 or newer
```
Without Python 3.9+: on macOS offer `xcode-select --install` (ships python3) or, if they use Homebrew, `brew install python`;
on Linux use the distro package (`python3`). Wait for the user to finish, then re-check.

## 2. Get the repo

Ask where to keep it; suggest `~/dev/skills/lanes`. If that directory already holds a lanes clone, ask, then run
`git -C <dir> pull --ff-only` instead of cloning. If it exists but is not a lanes clone, stop and ask for another path.
Create the parent directory first if it is missing. The repo may be private: use the user's existing git or `gh` access, and if access is denied, tell
them to ask the repo owner for access.

```
mkdir -p ~/dev/skills
git clone https://github.com/CosmosExmo/lanes.git ~/dev/skills/lanes
cd ~/dev/skills/lanes
```

## 3. See which subscriptions this machine can use

```
python3 scripts/lanes.py discover
```
No model calls. Summarize each line for the user as a short table: pool, what it is, status in plain words, and the CLI
that reaches it. Then:
- `usable`: ready. Mention how full its windows are.
- `logged_in_unverified`: a login exists, but it may be a cancelled plan. Ask whether they have an active subscription
  there. If they want it checked, run `python3 scripts/lanes.py discover --refresh --probe` (one tiny prompt per such
  pool) and report what changed. A cancelled plan shows up as `no_plan` and is never used.
- `api_billing` / billing `unknown`: pay-per-use. lanes will not run these unless the user explicitly approves spend
  for a given job. Say this plainly.
- `not_installed` / `not_logged_in`: nothing to do unless they want to set that CLI up.
- If `env` shows API-key variables (for example `OPENAI_API_KEY`), tell them the names and that lanes strips them from
  every run it starts, so those keys are never billed by lanes.

## 4. Link the skill and the command

```
python3 scripts/lanes.py install --dry-run
```
Show the planned links and ask for a yes. Then run `python3 scripts/lanes.py install`.
- If `~/.local/bin` is not on `PATH` (`command -v lanes` finds nothing), offer to add
  `export PATH="$HOME/.local/bin:$PATH"` to their shell rc file; edit it only after they agree.
- `install` covers `~/.agents/skills`, `~/.claude/skills`, `$CLAUDE_CONFIG_DIR/skills`, `~/.codex/skills`,
  `~/.gemini/antigravity-cli/skills` and `~/.cursor/skills` when they exist. Other harnesses need one setting that
  adds a skills *root* (a directory that contains skill folders; `~/.agents/skills` contains `lanes` after install):
  - Kimi: `extra_skill_dirs = ["~/.agents/skills"]` in `~/.kimi-code/config.toml`
  - OpenCode: `"skills": {"paths": ["~/.agents/skills"]}` in `~/.config/opencode/opencode.jsonc`
  - pi: add `~/.agents/skills` to the `skills` list in `~/.pi/agent/settings.json`
  - Qwen: link the repo as `~/.qwen/skills/lanes`
  If a key already exists, append to it instead of replacing it. Show the user the exact change and apply it only after
  they confirm. If a harness does not match any of these, tell the user and skip it rather than guessing.
- Claude Code: the repo has a `.claude-plugin/plugin.json`, so its link in a Claude Code skills directory also loads as a
  skills-directory plugin (`/plugin` lists `lanes@skills-dir`) with the two hooks from step 5. Nothing to configure.

## 5. Options

Ask about each, explain the trade-off, and write `~/.config/lanes/config.json` only with the keys they change:
- `cursor_read_quota` (default `false`): lets lanes read Cursor usage with Cursor's local login token via Cursor's
  internal usage endpoint. Without it, Cursor is tracked only by its limit errors.
- `agy_allow_shell` (default `false`): lets agy lanes run shell commands (for jobs that need tests). Leave off unless
  they want agy to take such jobs.
- `orchestrator_reserve_pct` (default `3`): share of the user's own session pool kept for the orchestrating agent.
- `prompt_hint` (default `true`): when a prompt asks for token-efficient work, quota relief, a second opinion or a
  cross-provider review (English or Turkish), a hook adds one line telling the agent to load the `lanes` skill. It
  helps when the agent has so many skills that it sees `lanes` by name only.
- `agent_guard` (default `false`): in Claude Code, refuses an `Agent`/`Task` call that does not name both `model` and `effort`,
  and tells the agent what to pass. Forks are exempt. Turn it on if they want The Rule enforced, not just taught.

The prompt hint also works in harnesses whose prompt-submit hook passes the prompt as `prompt` in its stdin JSON and
adds the hook's output to the context (Codex, Qwen and Kimi `UserPromptSubmit`). If the user wants it there, register
`python3 <repo>/hooks/lanes_hook.py hint` as that hook. Show the exact change to that harness's hooks config and apply
it only after they confirm. Skip harnesses whose prompt hook cannot add context.

## 6. Verify

```
lanes selftest            # offline: binaries, versions, flags (or python3 <repo>/scripts/lanes.py selftest)
```
Report the result. Offer `lanes selftest --live` (one cheap prompt per verified CLI) only if they want a live check.

## 7. Hand over

Tell the user, in their language:
- Restart agent sessions so the `lanes` skill loads (already-open sessions do not see new skills).
- From then on they just ask their agent, for example: "distribute this work with lanes", "get this diff reviewed by
  another provider", "my Claude limit is filling up, spread the load", "ask Gemini for a second opinion".
- To update later, they ask their agent to "update lanes" (it runs `lanes update`, which pulls and re-checks).
