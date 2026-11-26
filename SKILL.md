---
name: token-efficient-workflow
description: Plans delegation so the session's top model only orchestrates and synthesizes, while every subagent, workflow agent, fork or background job runs on an explicitly chosen cheaper model and effort, with a measured cost ledger. Use before spawning ANY subagent, Explore/Plan agent, workflow agent, parallel worker or delegated job (Claude Code, Codex, OpenCode, Kimi, pi), when about to leave the model parameter unset, delegate synthesis, or fan out more than 3 agents, and whenever the user asks for a token-efficient, cheap, quota-safe or "ucuz" workflow or investigation.
---

# Token-Efficient Workflow

Context isolation (subagents read, main session gets summaries) is half of token
efficiency. The other half is **tiering**: the session's model is the scarcest
quota, so it orchestrates and synthesizes and nothing else. Every spawn names
its model and effort; the inherited default is never used for a spawn.

## The Rule

```
NO SPAWN WITHOUT AN EXPLICIT MODEL + EFFORT. NEVER SPAWN THE SESSION'S OWN MODEL.
```

Why: an omitted `model` inherits the session model, and a family alias of the
session model (`opus` from an Opus session) resolves to that exact model. Built-in
`Explore`/`Plan` also run on the session model. Session effort does not carry
over to a different model, so an unset `effort` falls back to that model's default.

**Claude Code quick map** (T-tiers below):

| Session runs | T0 grunt | T1 trace | T2 judge | T3 synthesis |
|---|---|---|---|---|
| Opus | `haiku`/low | `sonnet`/medium | `sonnet`/**high** — never `opus` | you |
| Fable | `haiku`/low | `sonnet`/medium | `opus`/high | you |
| Sonnet | `haiku`/low | inline (sonnet is the session) | inline | you |

## Decide Before You Spawn (stop at the first rung that holds)

0. **Script it.** Counting, linting, diffing, schema checks, grep inventories →
   one `ctx_execute`/Bash script in the main session. Zero agent tokens.
1. **Do it inline.** A handful of tool calls, a known file, quick back-and-forth
   → main session. A fresh subagent re-pays for the context it lacks.
2. **Fork** only when the work needs most of the current conversation and is
   short: it reuses the session's prompt cache but runs on the session model.
3. **Fresh tiered subagent** for self-contained, verbose or parallel work that
   can return a summary. Everything below is about this case.

## Tier Rubric — classify by task shape, not by how important the bug is

| Tier | Task shape | Claude Code | Other harnesses | Effort |
|------|-----------|-------------|-----------------|--------|
| T0 grunt | grep/inventory, single-file lookup, run tests and report failures, doc fetch, form defaults | `haiku` | cheapest (mini/nano · flash · kimi highspeed · pi `scout`) | low |
| T1 trace | one flow across files with a precise question + schema; SELECT-only DB evidence; library/doc research; scoped edit of files it owns | `sonnet` | mid (gpt-5.x medium · k3 high · pi `researcher`/`worker`) | medium (low if paths are given) |
| T2 judge | refute ONE hypothesis; compare two designs; review one finding or one diff | one tier **below** the session: `sonnet` from Opus, `opus` from Fable | gpt-5.x xhigh · k3 max · pi `reviewer`/`oracle` | high — cap 4 per run |
| T3 synthesis | merge findings, decide root cause, write what the user reads | **main session, never spawned** | — | — |

Under-tiering is a failure too: T0 models miss branch conditions in multi-file
semantic tracing. "Does the stored value ever differ from the input?" is T1.

## Shape of a Spawn Prompt

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

**Write-capable agents:** back up first, give each agent a disjoint file set,
review the combined diff in the main session (T3), then at most one T2 review.

## Measure Every Run (the ledger)

Each Agent completion carries `<usage>` (`subagent_tokens`, `tool_uses`,
`duration_ms`); `/tasks` shows each running agent's model and effort; `/workflows`
shows per-agent tokens. Keep one ledger row per spawn and report the totals:

| label | tier | model/effort | tokens | duration | outcome (used / reworked / wasted) |

Fixed overhead is real: on this machine a fresh `general-purpose` subagent used
~50 k tokens with zero tool calls (system prompt, tools, CLAUDE.md, skill listing;
measured 2026-11-09). Work smaller than that belongs inline (rung 1). For read-only
search prefer `Explore` with an explicit `model`: it starts without CLAUDE.md.

Review it before the next fan-out: a T0 row that needed a rerun → re-tier to T1;
a T1/T2 row whose output was ignored → cut it. Try a large fan-out on a small
slice first. Limits: 20 concurrent subagents; workflow warns above 25 agents or
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

## Red Flags — STOP and re-tier

- An `Agent(`/`agent(`/`spawn_agent(`/subagent call with no `model` or no `effort`
- `subagent_type: Explore|Plan` without `model`
- The session model's name or family alias in any spawn
- A spawn labelled "synthesis", "final report", "root cause verdict"
- More than 4 T2 agents, or a verifier per finding instead of per hypothesis
- A brief without an output schema or without the `file:line` requirement
- A fan-out that ends without a ledger total

Per-harness call syntax, config keys and this machine's state: `harnesses.md`.
