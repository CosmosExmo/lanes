# Changelog

## 2.0.0 — 2026-11-26

- Renamed from token-efficient-workflow to lanes.
- New `lanes` CLI: discovers every subscription pool, ranks lanes by use-it-or-lose-it quota, and runs other agent CLIs headless.
- Eight CLIs supported: claude, codex, agy, cursor-agent, pi, opencode, kimi (unverified), qwen (unverified).
- Write lanes run in git worktrees and are judged by their diff, not exit code.
- Reviews are routed to a different provider than the writer.
- Ledger of every lane job with `lanes mark` outcomes and `lanes ledger` totals.
- No silent spend: api/unknown billing needs `--allow-spend`; API-key variables are stripped from children; nested delegation is refused.
- `lanes install`, `lanes update` and `lanes selftest [--live]`.
