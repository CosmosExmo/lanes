# Changelog

## Unreleased

- Safety: a lane must belong to its pool's discovered access; overage pools are re-checked at run time (exit 4); write modes need `--worktree`; `run --from <pool>|none` replaces a silently skipped own-pool check.
- Billing: pi and opencode billing follows the credential type; every adapter strips all known provider key variables.
- Runner: `partial` status (edited, then failed), orphaned agent process groups are reaped, ledger is 0600 with cache-relative worktrees, locked inventory writes, rollback of half-prepared jobs, empty answers are `failed`.
- agy read mode grants read-only access through `--settings`; `denied_actions` are reported.
- Ranking: stale (already reset) windows are quota-unknown and trigger a refresh; cursor `auto` is a lane.
- Parallel discovery (4 workers), sturdier quota and ledger parsing.

## 2.0.0 — 2026-11-26

- Renamed from token-efficient-workflow to lanes.
- New `lanes` CLI: discovers every subscription pool, ranks lanes by use-it-or-lose-it quota, and runs other agent CLIs headless.
- Eight CLIs supported: claude, codex, agy, cursor-agent, pi, opencode, kimi (unverified), qwen (unverified).
- Write lanes run in git worktrees and are judged by their diff, not exit code.
- Reviews are routed to a different provider than the writer.
- Ledger of every lane job with `lanes mark` outcomes and `lanes ledger` totals.
- No silent spend: api/unknown billing needs `--allow-spend`; API-key variables are stripped from children; nested delegation is refused.
- `lanes install`, `lanes update` and `lanes selftest [--live]`.
