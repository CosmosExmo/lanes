# Changelog

## 2.0.2 — 2026-11-26

- Lane results always reach the orchestrating session: SKILL.md now says how to start a lane per harness (Claude Code: `lanes run` without `--bg` as a background Bash command, so the harness notifies you; elsewhere a blocking `lanes run`, or `--bg` followed immediately by `lanes wait`).
- `lanes run --bg` warns on stderr that nothing will notify you and prints the `lanes wait` command to collect the job.

## 2.0.1 — 2026-11-26

- Install with your agent: paste the prompt from the README; the agent follows `INSTALL.md` interactively (discovers subscriptions, asks before each change, optional probes).
- New app icon.

## 2.0.0 — 2026-11-26

- Renamed from token-efficient-workflow to lanes.
- New `lanes` CLI: discovers every subscription pool, ranks lanes by use-it-or-lose-it quota, and runs other agent CLIs headless.
- Eight CLIs supported: claude, codex, agy, cursor-agent, pi, opencode, kimi (unverified), qwen (unverified).
- Write lanes run in git worktrees and are judged by their diff, not exit code.
- Reviews are routed to a different provider than the writer.
- Ledger of every lane job with `lanes mark` outcomes and `lanes ledger` totals.
- No silent spend: api/unknown billing needs `--allow-spend`; API-key variables are stripped from children; nested delegation is refused.
- `lanes install`, `lanes update` and `lanes selftest [--live]`.
- Safety: a lane must belong to its pool's discovered access; overage pools are re-checked at run time (exit 4); write modes need `--worktree`; `run --from <pool>|none` replaces a silently skipped own-pool check.
- Billing: pi and opencode billing follows the credential type; every adapter strips all known provider key variables.
- Runner: `partial` status (edited, then failed), orphaned agent process groups are reaped, ledger is 0600 with cache-relative worktrees, locked inventory writes, rollback of half-prepared jobs, empty answers are `failed`.
- agy read mode grants read-only access through `--settings`; `denied_actions` are reported.
- Ranking: stale (already reset) windows are quota-unknown and trigger a refresh; cursor `auto` is a lane.
- Parallel discovery (4 workers), sturdier quota and ledger parsing.
