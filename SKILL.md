---
name: lanes
description: Routes agent work across every AI subscription on the machine (Claude, ChatGPT/Codex, Google/agy, Cursor, pi, OpenCode, Kimi, Qwen) by remaining quota and model strength, sends reviews to a different provider, and keeps every spawn token-efficient with an explicit model and effort. Use before spawning any subagent, Explore/Plan agent, fork, workflow, parallel agents or workers; when the user wants token-efficient or cheaper work, in any language, even for a small task; when a usage limit or quota is filling up ("weekly is at 84%"); for a second opinion or cross-provider review ("ask Codex", "ask Gemini", "have another model review this"); or to install or update lanes.
when_to_use: Typical requests - "work token-efficiently", "do this cheaply", "my Claude weekly limit is at 84%, spread the work", "have another model review this diff", "get a second opinion from Gemini", "spread this across my subscriptions", "get this reviewed by another provider", "run these in parallel without burning my Opus quota", "install lanes", "update lanes". The same requests in any other language count too.
allowed-tools: Bash(lanes *)
---


# Lanes

Two levers. **Tiering:** the session's model is the scarcest quota, so it
orchestrates and synthesizes and nothing else; every spawn names its model and
effort. **Pools:** each subscription (Claude, ChatGPT/Codex, Google/agy, Cursor,
...) has its own limit that resets and expires unused. A pool at 84% with days
left should not carry the bulk while another pool sits at 20%. The bundled
`lanes` CLI discovers the pools, ranks lanes (pool + CLI + model + effort), runs
them headless and keeps the ledger. Any harness can be the orchestrator.

## The Rule

```
NO SPAWN WITHOUT AN EXPLICIT MODEL + EFFORT. NEVER SPAWN THE SESSION'S OWN MODEL.
```

An omitted `model` inherits the session model, and a family alias of the session
model (`opus` from an Opus session) resolves to that exact model. Built-in
`Explore`/`Plan` also run on the session model. Session effort does not carry
over to a different model, so an unset `effort` falls back to that model's default.

**Claude Code quick map** (T-tiers below):

| Session runs | T0 grunt | T1 trace | T2 judge | T3 synthesis |
|---|---|---|---|---|
| Opus | `haiku`/low | `sonnet`/medium | `sonnet`/**high** — never `opus` | you |
| Fable | `haiku`/low | `sonnet`/medium | `opus`/high | you |
| Sonnet | `haiku`/low | inline (sonnet is the session) | inline | you |

## Collect every lane

Your session must hear back from every lane it starts. `--bg` detaches the job from your harness: nothing will
notify you when it ends. Start lanes one of these two ways, and never end a turn with a lane that is neither:

| Your harness | Start the lane | The result reaches you via |
|---|---|---|
| Claude Code (or any harness whose background shell notifies you on exit) | your Bash tool with `run_in_background: true`, running `lanes run ...` **without** `--bg` (parallel lanes: one background command each) | the harness's completion notification; then read that command's output |
| Codex, Cursor, agy, or any harness without such notifications | one lane: `lanes run ...` without `--bg` (it blocks and returns the answer). Several in parallel: `lanes run ... --bg` per job, each prints a job id | the blocking `lanes run` itself; for `--bg` jobs, `lanes wait <id> [<id> ...]` as your very next command |

`lanes wait` is a command you run, not something that wakes you: do not end the turn or answer the user between
starting `--bg` jobs and waiting for them.

## Red Flags — STOP and re-plan

- An `Agent(`/`agent(`/`spawn_agent(`/subagent call with no `model` or no `effort`
- `subagent_type: Explore|Plan` without `model`
- The session model's name or family alias in any spawn
- A spawn labelled "synthesis", "final report", "root cause verdict"
- Delegating with no `lanes pick` (or `lanes discover`) before it, or no Plan line
- A review whose pool equals the pool that wrote the diff
- A write lane without `--worktree`, or a write called done from the exit code
- Ending a turn after `lanes run --bg` without `lanes wait` on that id, or expecting a notification for a `--bg` job
- `--allow-spend` without the user's yes, or an `approval=True` row run silently
- More than 4 T2 agents, or a verifier per finding instead of per hypothesis
- A brief without an output schema or without the `file:line` requirement
- A fan-out that ends without a ledger total

## Decide Before You Spawn (stop at the first rung that holds)

0. **Script it.** Counting, linting, diffing, schema checks, grep inventories →
   one script in the main session. Zero agent tokens.
1. **Do it inline.** A handful of tool calls, a known file, quick back-and-forth.
   A fresh subagent or lane re-pays for the context it lacks, and a lane adds CLI
   start-up (seconds, sometimes tens of thousands of tokens).
2. **Fork** only when the work needs most of the current conversation and is
   short: it reuses the session's prompt cache but runs on the session model.
3. **Tiered subagent in your own pool** (native `Agent`/`spawn_agent`/pi
   `subagent`, syntax in `harnesses.md`) for self-contained, verbose or parallel
   work that returns a summary.
4. **Lane in another pool** when your pool is braked (pace above 1) or short on
   headroom, when the work is a review, or when another model family fits it
   better. This is where quota is actually saved. Next section.

## Lanes in other pools

Run `lanes` if it is on PATH, otherwise `python3 <skill dir>/scripts/lanes.py`
(`lanes install --bin-only` puts it on PATH). Python 3, stdlib only. To update: `lanes update`; to install for a new user, follow `INSTALL.md`.

```
lanes discover                       # once per session: pools, billing, quota (cached 15 min). No model calls.
lanes pick --tier T1 --kind write --task code --n 3
lanes run --pool P --cli C [--provider V] --model M --effort E --mode write --worktree \
          --prompt-file brief.md --tier T1 --kind write --task code   # blocks until done, prints status + answer
lanes wait <id> [<id> ...]           # collects --bg jobs: one blocking call for all of them; never poll in a loop
lanes mark <id> used|reworked|wasted # outcome into the ledger; `lanes ledger` prints totals
```

**Plan line.** Before any delegation, fill this in and show it. An empty slot
means you have not decided yet:

```
work: <kind, tier> | own pool: <pool, window %, pace> | writer/runner: <pool cli model effort or native> |
reviewer: <pool, different from the writer's pool> | spend: none
```

**Reading `pick`.** `--tier T0|T1|T2` is the task shape (rubric below),
`--kind read|write|review`, `--task code|review|research|plan|docs|ui` steers toward model strengths,
`--needs-shell` skips lanes that cannot run shell. Each row: `pool cli provider/model effort fits urgency brake
approval reason`. Rows are ranked, so take the first usable row; `--n 3` gives fallbacks.
- `cli = native` is your own pool: use a tiered subagent, not `lanes run`.
- `brake=True` means that pool's pace is above 1 and the job is not small; the row sorts later.
- `approval=True` means api- or unknown-billed. Skip it unless the user says yes to spend.
- A lane that cannot fit the job in its remaining quota is already dropped.
- `pick` knows your own pool from the harness environment. If it cannot tell, pass `--from <pool>`
  (`--from none` when you are in no pool); `run` refuses with exit 2 until it knows.
- A pool whose cached window already reset is `stale`: `pick` refreshes the inventory first and, if the new
  window still cannot be read, ranks the pool as quota-unknown.

**`run` flags** come from the `pick` row: `--pool --cli --provider --model --effort`, plus `--from <pool>|none`
when your own pool is not detected. The lane must be one `discover` found for that pool (exit 2 otherwise).
`--mode read|write|write_shell`; `write` and `write_shell` need `--worktree` or `--resume` (a fresh git worktree
on branch `lanes/<job>`; exit 2 without). `--bg` returns at once; without it
`run` waits and prints the answer. Answers land in `<cache>/jobs/<job>/out.txt`
(`$LANES_CACHE`, default `~/.cache/lanes`); `lanes wait --json` also gives `worktree` and `branch`.
Exit codes: 0 ok, 2 usage/environment or wait timeout, 3 nested, 4 approval needed (api/unknown billing, or the job
would cross an overage pool's plan limit), 6 job failed, limit, partial or no_changes. An exit-0 run with an empty
answer is `failed` ("empty answer"); denied tools are named in the job's `note`.

### Routing rules

1. **Own pool when it has room.** `cli = native` marks your own pool wherever it ranks; if it is the first
   usable row and your window is not braked, use a tiered subagent. `pick` already deducts a small reserve
   from your own pool for synthesis. Go to another pool when your pace is above 1, the pool is exhausted, the
   work suits another family, or it is a review.
2. **Use it or lose it.** Unspent quota burns at the reset. Take `pick`'s first usable row; it already ranks by
   remaining % per hour to reset. Quota tail: short T0, review and read jobs go to a pool near its limit, long
   jobs go elsewhere, and a lane that cannot fit a job is dropped only for that job. No fixed threshold, and a high
   Claude percentage is not a reason to stop using Claude: it is a reason to send bulk and review elsewhere
   and keep your own pool for T3 synthesis.
3. **Review leaves the writer's pool.** `lanes pick --kind review --task review --tier T2 --author-pool <writer pool>`
   (T1 for small diffs). The writer pool is the pool of the lane or job that produced the diff: its `lanes run`
   row or ledger line; if you wrote it yourself, your own pool. For security, money, migration or
   infrastructure changes take two reviewers from two different pools, neither the writer's.
4. **Money.** Lanes never cross a plan limit on their own: `run` re-reads the quota of overage-capable pools and
   refuses (exit 4) a job that would not fit, even if `pick` listed it. api/unknown billing needs `approval`; pass
   `--allow-spend` only after the user agreed to that spend in this conversation.
5. **Writes** run `--mode write --worktree`, one disjoint file set per lane. A write is judged by the diff
   (`git -C <worktree> diff`), never by exit code: status `done` means files changed, `no_changes` is a failure,
   and `partial` means files changed but the run then failed or timed out (worktree kept: read the diff, then
   accept it, `--resume` it or discard it).
   Read the diff yourself (T3), run the tests, merge the branch. If it needs shell (tests), `--needs-shell`.
6. **No silent fallback.** A failed or `limit` lane is reported to the user in one line; you then pick the next
   `pick` row yourself and `lanes mark <id> wasted`. A limit-cut write continues with `--resume <job>` in the same worktree.
7. **No nesting.** Lane prompts say "do not delegate". `lanes run` exits 3 inside a lane: then do the work yourself.
8. **Brief a lane like any spawn** (shape below) and put what it must return at the end. It starts with no
   context, no skills and no memory of this conversation.
9. **Anthropic only through `claude`.** Claude models are reached with the `claude` CLI; Anthropic logins inside
   pi or opencode are never used.

Per-CLI invocation details, quota sources, billing traps and orchestrating from each harness: `harnesses.md`.

## Tier Rubric — classify by task shape, not by how important the bug is

| Tier | Task shape | Claude Code | Other harnesses | Effort |
|------|-----------|-------------|-----------------|--------|
| T0 grunt | grep/inventory, single-file lookup, run tests and report failures, doc fetch, form defaults | `haiku` | cheapest (mini/nano · flash · kimi highspeed · pi `scout`) | low |
| T1 trace | one flow across files with a precise question + schema; SELECT-only DB evidence; library/doc research; scoped edit of files it owns | `sonnet` | mid (gpt-5.x medium · k3 high · pi `researcher`/`worker`) | medium (low if paths are given) |
| T2 judge | refute ONE hypothesis; compare two designs; review one finding or one diff | one tier **below** the session: `sonnet` from Opus, `opus` from Fable | gpt-5.x xhigh · k3 max · pi `reviewer`/`oracle` | high — cap 4 per run |
| T3 synthesis | merge findings, decide root cause, write what the user reads | **main session, never spawned** | — | — |

Under-tiering is a failure too: T0 models miss branch conditions in multi-file
semantic tracing. "Does the stored value ever differ from the input?" is T1.
Research that only needs docs or a repo read is T0/T1 on a lane, never T2.

## Shape of a Spawn Prompt (native subagent or lane)

1. Paths + what is already known and ruled out (no re-derivation).
2. ONE precise question or ONE owned file set per agent; the symptom it serves.
3. Token rules: `grep -n` to locate, `sed -n 'A,Bp'` windows ≤60 lines, never cat
   whole files or minified bundles, skip generated files, evidence ≤300 chars,
   `file:line` on every claim, no prose.
4. Structured output schema (findings / hypotheses / changes / open_questions).
5. Explicit `model` + `effort` in the call, not only in the prompt text.

**Cache-friendly fan-out:** agents with the same model, effort, agent type, tools,
schema and cwd share a prompt-cache prefix. Put the shared brief first and the
per-agent part (file list, hypothesis) last; batch spawns by tier.

**Write-capable agents:** back up first (a lane's worktree is the backup), give
each agent a disjoint file set, review the combined diff in the main session
(T3), then one review from another pool (rule 3).

## Measure Every Run (the ledger)

`lanes` writes one ledger row per lane job (pool, model, effort, duration, status,
quota delta); `lanes mark` adds the outcome and `lanes ledger [--since 1d]` totals
it. Native spawns: each Agent completion carries `<usage>` (`subagent_tokens`,
`tool_uses`, `duration_ms`); `/tasks` shows each running agent's model and effort.
Report one line per spawn and the totals:

| label | tier | pool + model/effort | tokens or quota Δ | duration | outcome (used / reworked / wasted) |

Fixed overhead is real: one measurement showed a fresh `general-purpose` subagent
using ~50 k tokens with zero tool calls (system prompt, tools, memory files, skill
listing). Work smaller than that belongs inline (rung 1). For read-only search
prefer `Explore` with an explicit `model`: it starts without the memory files.

Review it before the next fan-out: a T0 row that needed a rerun → re-tier to T1;
a row whose output was ignored → cut it. Try a large fan-out on a small slice
first. Limits: 20 concurrent subagents; workflow warns above 25 agents or
1.5 M projected tokens.

## Rationalizations Seen in Real Runs

| Excuse | Reality |
|--------|---------|
| "Root cause is probably here, needs full reasoning → omit model" | A tracer collects evidence against a schema; reasoning is T3 in the main session. T1. |
| "Spawn a synthesis/report agent with the session model" | Main session already holds the findings; a spawned copy pays for the context twice. |
| "Explore is the cheap search agent" | Built-in Explore/Plan inherit the session model unless you pass `model`. |
| "Pass `opus`, it's only one agent" | From an Opus session `opus` resolves to the session model itself. |
| "Fork it, it already has the context" | A fork runs on the session model; fine for one short step, never for a fan-out. |
| "Tool doc says default to omitting model" | That default optimizes uncapped accounts. On a quota, omit = top model on grunt work. |
| "Everything on haiku is cheapest" | Haiku misses multi-file branch conditions → wrong verdict → rerun. Match tier to shape. |
| "Session effort is fine for the critical step" | Effort is per spawn; it does not carry across models. `high` only on T2. |
| "One agent per finding/file is thorough" | One per hypothesis or per owned file set; cap T2 at 4 and log what was capped. |
| "84% means stop using Claude" | It means stop spending it on bulk. Writes, research and reviews go to the pools `pick` ranks first; Claude keeps T3. |
| "My pool has room, so everything stays native" | Check pace, not just percent: 84% with 5 days left is pace above 1. Look at the other pools before choosing. |
| "Exit 0, so the write worked" | agy exits 0 when it was denied a tool. Success is a non-empty `git diff` in the worktree. |
| "Other CLIs are only for second opinions" | They are lanes for writes, research and reviews. A second opinion is one use. |
| "The reviewer can be the same provider, it is a different model" | Same pool = same quota and same blind spots as the writer. `--author-pool` excludes it. |
| "`lanes discover` is slow, I will guess the quotas" | One 20-60 s call, then cached 15 min. Guessed quotas route the bulk to the wrong pool. |
| "I started it with `--bg`, I will be notified when it finishes" | `--bg` detaches from the harness; no notification ever comes. In Claude Code run `lanes run` (no `--bg`) as a background Bash command; elsewhere `lanes wait` before ending the turn. |
| "It failed, so I will quietly do it myself" | Say it failed, take the next `pick` row, mark the dead one `wasted`. Doing it inline spends the top model. |
