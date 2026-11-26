<p align="center"><img src="assets/icon.svg" width="96" height="96" alt="lanes icon"></p>

# lanes

Spread agent work across all your AI subscriptions.

## What it is

`lanes` is an agent skill plus a small Python CLI. The skill teaches whichever agent you work in (Claude Code, Codex, agy, Cursor, pi, OpenCode, Kimi, Qwen) to delegate by pool: each subscription you pay for is a separate lane with its own limit. The CLI discovers your pools, ranks lanes, runs other agent CLIs headless (in git worktrees when they write), sends reviews to a different provider, and keeps a ledger. Python 3.9+, standard library only, macOS and Linux.

## Why

- Subscription windows reset. Quota you do not use before the reset is lost, so a pool sitting at 20% should carry work while one at 84% rests.
- One provider should not carry everything. Your session model is your scarcest quota: it orchestrates and synthesizes, the lanes do the bulk.
- A reviewer from another provider catches what the writer's model misses. Reviews never go back to the pool that wrote the diff.

## How it works

discover → pick → run → review → ledger

```
lanes discover                                   # pools, billing, quota (cached 15 min, no model calls)
lanes pick --tier T1 --kind write --task code --n 3
lanes run --pool P --cli C --model M --effort E --mode write --worktree \
          --prompt-file brief.md --bg --tier T1 --kind write --task code     # prints a job id
lanes wait <id>                                  # one blocking call; branch lanes/<id> holds the diff
lanes pick --tier T2 --kind review --task review --author-pool P             # reviewer from another pool
lanes mark <id> used                             # used | reworked | wasted
lanes ledger --since 1d                          # totals
```

`pick` ranks lanes by remaining quota per hour to reset (use it or lose it), drops lanes that cannot fit the job, and lists lanes that need approval to spend last, marked `approval=True`.

## Supported CLIs

| CLI | Pools it reaches | Quota source | Verified version | Notes |
|---|---|---|---|---|
| `claude` | Anthropic | `claude -p "/usage"` (no tokens) | 2.1.332 | The only way Anthropic is reached; Anthropic logins inside pi/opencode are ignored |
| `codex` | ChatGPT | `codex app-server` rate-limit RPC | 0.172.0 | Credit balance can be spent after the limit, so a run that would cross the plan limit needs approval |
| `agy` | Google, plus the Claude/GPT groups on that plan | `agy -p "/usage"` (no tokens) | 1.5.18 | Cannot run shell headless unless `agy_allow_shell` is set |
| `cursor-agent` | Cursor | none official; opt-in via `cursor_read_quota` | 2026.11.22-e9042b2 | Monthly window; on-demand billing on paid plans |
| `pi` | Multi-provider: routed to the provider's pool | read from that pool's own CLI | 1.6.0 | Only an OAuth credential is the subscription; any other credential type is API billing |
| `opencode` | Multi-provider: routed to the provider's pool | none (reactive limit tracking) | 2.3.2 | Run with `--standalone` |
| `kimi` | Kimi | none (reactive limit tracking) | 2.7.2, unverified | Not routed until a probe passes |
| `qwen` | Qwen | none (reactive limit tracking) | 0.28.3, unverified | Not routed until a probe passes |

"Verified" means checked live against that version. Other versions may work; `lanes selftest` reports flag drift.

## Install

```
git clone https://github.com/CosmosExmo/lanes.git ~/dev/skills/lanes
cd ~/dev/skills/lanes
python3 scripts/lanes.py discover              # works straight from the checkout, before install
python3 scripts/lanes.py install --dry-run     # show what would be linked
python3 scripts/lanes.py install               # do it
```

`install` links `scripts/lanes.py` as `~/.local/bin/lanes` (make sure that is on `PATH`) and links the repo as `lanes` into each skills directory that exists: `~/.agents/skills`, `~/.claude/skills`, `~/.codex/skills`, `~/.gemini/antigravity-cli/skills`, `~/.cursor/skills`, and `$CLAUDE_CONFIG_DIR/skills`. An existing real directory with that name is left alone. `--bin-only` links only the command.

## Commands

| Command | What it does |
|---|---|
| `lanes discover [--refresh] [--probe] [--json]` | Find pools, billing and quota. `--probe` sends one prompt per pool (about eight model calls), so use it deliberately |
| `lanes pick --tier T0\|T1\|T2 --kind read\|write\|review [--task T] [--needs-shell] [--author-pool P] [--from P] [--n N]` | Ranked lanes for a job |
| `lanes run --pool --cli [--provider V] --model --effort --mode read\|write\|write_shell --prompt-file F [--from POOL\|none] [--worktree] [--bg] [--resume ID] [--allow-spend]` | Run one lane headless. `--provider` is for multi-provider CLIs (pi, opencode); `--from` names your own pool when it cannot be detected; writes need `--worktree` or `--resume` |
| `lanes wait <id>...` | Block until the jobs finish |
| `lanes mark <id> used\|reworked\|wasted` | Record the outcome |
| `lanes ledger [--since 1d]` | Ledger totals |
| `lanes selftest [--live]` | Check installs and adapter flags; `--live` runs one cheap probe per verified CLI |
| `lanes install [--bin-only] [--dry-run]` | Link the command and the skill |
| `lanes update` | `git pull --ff-only` in the repo |

Exit codes: 0 ok; 2 usage/environment error or wait timeout; 3 nested delegation refused; 4 approval needed; 5 selftest failure; 6 job failed, hit a limit, made no changes, or was `partial` (edited, then failed). Most commands take `--json`.

## Safety

- No silent spend. Overage-capable pools never cross the plan limit automatically: `run` re-reads the quota and refuses (exit 4) a job that would not fit. `api` or `unknown` billing is not run without `--allow-spend`, which you pass only after agreeing to the spend. A lane must be one that `discover` found for its pool, so billing always comes from the pool that runs.
- API-key variables (OpenAI, Anthropic, Google, Cursor, Moonshot, xAI, DeepSeek, Z.ai, OpenRouter and similar) are stripped from every child's environment so a stray key cannot switch a run to paid billing.
- No nested delegation. A child gets `LANES_DEPTH=1`, and `lanes run` refuses (exit 3) when it is set.
- Writes happen in a git worktree on branch `lanes/<job>`, never in your checkout (`write` needs `--worktree`). A write is judged by its diff, never by exit code: `done` means files changed, `partial` means files changed but the run failed (worktree kept), `no_changes` is a failure.
- Children read their prompt from a file and get no stdin (`/dev/null`), except `claude`, which reads the prompt file on stdin.
- Private data stays out of the inventory and ledger: they hold pool ids, models, numbers and cache-relative worktree paths, not accounts, emails, tokens or prompts, and anything echoed is redacted. Prompts and answers are stored locally under `jobs/<id>/` (mode 0600) and never in the ledger.
- Reading Cursor quota needs a local token, so it is off unless you set `cursor_read_quota`.

## Configuration

Optional file `~/.config/lanes/config.json` (override with `$LANES_CONFIG`):

| Key | Default | Meaning |
|---|---|---|
| `cursor_read_quota` | `false` | Allow reading Cursor usage with the local token |
| `agy_allow_shell` | `false` | Allow agy lanes to run shell commands |
| `orchestrator_reserve_pct` | `3` | Share of your own pool's window held back for synthesis |

State lives in `~/.cache/lanes` (override with `$LANES_CACHE`): `inventory.json`, `ledger.jsonl`, `jobs/<id>/`, `worktrees/<id>/`.

## Updating

```
lanes update            # git pull --ff-only
lanes selftest          # offline checks; add --live for one cheap probe per verified CLI
```

## Development

```
python3 -m unittest discover -s tests -v
```

`adapters.json` (per-CLI flags, run templates, provider routing) and `capabilities.json` (model strengths) are data: new CLI versions and models are edits there, not code. See `SKILL.md` for the agent-facing rules and `harnesses.md` for per-harness details.
