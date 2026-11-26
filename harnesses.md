# Harness reference — delegation + per-agent model/effort

Contents: Claude Code · Codex CLI · OpenCode · Kimi Code · pi · Gemini CLI · Copilot CLI · Verified 2026-11-26 (Task 7)

Verified 2026-11-09 against installed binaries/packages (codex 0.166.0, opencode 2.2.5,
kimi 2.5.2, pi 1.5.0 + pi-subagents 0.86.0, Claude Code 2.1.319) and code.claude.com docs.
Gemini/Copilot sections are from 2026-08-26 and unverified since. Re-check after a major
version bump: grep the binary's strings / bundled docs rather than trusting this file.

## Claude Code

- `Agent` tool: `subagent_type`, `model: haiku|sonnet|opus|fable` (aliases only, no full IDs or
  `inherit`), `effort: low|medium|high|xhigh|max` (v2.1.292+), `run_in_background`, `isolation: worktree`.
  `subagent_type: "fork"` = full context + session model + shared prompt cache.
- Model resolution: call param → agent file `model` → `CLAUDE_CODE_SUBAGENT_MODEL` → session model.
  A family alias equal to the session's family resolves to the session's exact model.
  Built-in `Explore`/`Plan` run on the session model (from Fable: on Opus) unless `model` is passed.
  Optional safety net: `CLAUDE_CODE_SUBAGENT_MODEL=sonnet` in settings `env` catches omitted models
  (not set on this machine; doesn't affect Explore/Plan or forks).
- Effort: session effort applies only to subagents on the session model; on another model set it.
- `Workflow` tool (only when the user opts in): `agent(prompt, {label, phase, schema, model, effort,
  agentType, isolation})`, `parallel()`, `pipeline()`. Agents with identical model/effort/type/tools/
  schema/cwd share a cache prefix; workflow cache TTL 5 min (`subagentPromptCacheTtl: "1h"` to extend).
- Measuring: each Agent completion carries `<usage><subagent_tokens>…<tool_uses>…<duration_ms>…`;
  `/tasks` shows model + effort per running agent; `/workflows` per-agent tokens; `/usage` totals;
  `/skill-doctor` per-skill context cost and invocation counts.
- Limits: 20 concurrent subagents (`CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`); workflow "Large" warning
  above 25 agents or 1.5 M projected tokens.

Tiered Workflow excerpt (tracers → capped refuters → synthesis in main session; Opus session):

```js
const TRACERS = [
  { key: 'admin-form-defaults', model: 'haiku',  effort: 'low',    prompt: ... }, // T0
  { key: 'api-create-flow',     model: 'sonnet', effort: 'medium', prompt: ... }, // T1
  { key: 'prod-db-evidence',    model: 'sonnet', effort: 'medium', prompt: ... }, // T1
]
const traced = await parallel(TRACERS.map(t => () =>
  agent(t.prompt, { label: `trace:${t.key}`, schema: FINDINGS, model: t.model, effort: t.effort })))
const hyps = traced.flatMap(t => t.hypotheses.filter(h => h.confidence !== 'low')).slice(0, 4)
const verdicts = await parallel(hyps.map(h => () =>
  agent(refute(h), { schema: VERDICT, model: 'sonnet', effort: 'high' }))) // T2: one tier below Opus
return { traced, verdicts } // T3 synthesis happens in the main session
```

## OpenAI Codex CLI (0.166)

- Tools: `spawn_agent` (`task_name`, `agent_type`, `model`, `reasoning_effort`, `fork_turns: none|all`,
  `nickname`), `followup_task`, `send_message`, `wait_agent`, `interrupt_agent`, `list_agents`.
  `spawn_agents_on_csv` / `report_agent_job_result` are gone from the binary.
- Gated: spawns only when the user or AGENTS.md/skill text explicitly asks for sub-agents or
  delegation ("be thorough" does not count) — this skill's text is what unlocks it.
- Per-spawn `model`/`reasoning_effort` are exposed when `[features.multi_agent_v2]
  expose_spawn_agent_model_overrides = true`; the tool text still says to omit them unless asked,
  so cite this skill when setting them. Fallback for every spawn: `[agents] default_subagent_model`,
  `default_subagent_reasoning_effort`. Roles can pin model/effort.
- Effort values: `none|minimal|low|medium|high|xhigh`.

## OpenCode (2.2.5)

- Subagent = agent with `mode: "subagent"` (`"primary"|"all"` also valid) in
  `~/.config/opencode/opencode.jsonc` → `agent.<name>.{description, model, permission, steps, hidden}`.
- Model: `"provider/model#variant"` or `{providerID, model, variant}`; effort comes from the variant,
  defined at `provider.<p>.models.<m>.variants.<id>.settings` (e.g. a reasoning-effort option).
  The spawn tool also takes `model`, but its text says not to set it unless asked — prefer pinning
  `model` on cheap dedicated subagent definitions.
- Source: `strings $(brew --prefix)/Cellar/opencode/*/bin/opencode` (Config.Agent schema),
  https://opencode.ai/docs/agents , /docs/models

## Kimi Code (2.5.2)

- Tools: `Agent` (`description`, `subagent_type`, `fork`, `model`, `resume`, background) and
  `AgentSwarm` (batch spawn, one shared `model`). Background results are fed back automatically.
- `model` must be an alias from the `[secondary_model]` pool; with no pool, subagents use
  `default_model`. No per-call effort: it comes from `[models."<alias>"] default_effort`
  (`support_efforts`) or global `[thinking] effort`. `secondary_model.force` is obsolete.
- Source: `@moonshot-ai/kimi-code/dist/main.mjs` (Agent/AgentSwarm zod schema),
  https://moonshotai.github.io/kimi-code/en/configuration/config-files.html

## pi (1.5.0, pi-subagents 0.86.0)

- Tool `subagent` (enable with `subagents_enable({})`; `/run <role>[model=provider/id:level] "task"`).
  Per-launch `model` (exact `provider/id`, optional `:thinking` suffix) and `thinking` params.
- Precedence: per-run override → `agentOverridesByProvider.<provider>.<role>` →
  `agentOverrides.<role>.{model,thinking,fast}` → agent frontmatter → `defaultModel`/parent.
- `modelScope {enforce, allow}` only rejects out-of-scope models; it never picks a cheaper one.
- One model per launch, no fallback chains: if it fails, relaunch explicitly with another in-scope
  model. `fast: true` only for interactive loops (burns quota).

## Gemini CLI (2026-11-25)

- No longer serves free, AI Pro or Ultra accounts since 2026-06-18 (`UNSUPPORTED_CLIENT`); only API key,
  Vertex or Code Assist Standard/Enterprise. Not installed here; subscription capacity is `agy`.

## Antigravity CLI — `agy` (verified 2026-11-25, 1.5.16)

- Subagents: `~/.gemini/config/agents/<name>.md` (workspace `.agents/agents/`), frontmatter `name`,
  `description`, `tools`, `model` (`inherit|flash|pro|flash_lite`); `agy --agent <name>`, `agy agents`.
- Headless: `agy -p "..." --model <slug> --effort low|medium|high|xhigh|max --mode accept-edits|auto|plan
  --output-format text|json|stream-json --print-timeout 0s`. Tools needing approval are soft-denied
  (exit 0); blanket `--dangerously-skip-permissions`, scoped `permissions.allow` in settings.
- Auth: Google account in Keychain = AI Pro/Ultra quota. API billing only with `modelProvider: "gemini"`
  plus `GEMINI_API_KEY` (key alone has no effect; `.env` files are not read).
- Skills: does NOT read `~/.agents/skills`; sync mirrors skills-all into `~/.gemini/antigravity-cli/skills`.
  The server-side customization budget excludes some of ~200 skills (listed by name only).
- Source: antigravity.google/docs (cli/reference, cli/headless, subagents, install) + embedded hooks doc.

## GitHub Copilot CLI (unverified since 2026-08-26)

- Custom agents JSON `agents: [{name, entrypoint, model, tools}]`, `/agent` or `--agent <name>`;
  no in-session spawn tool, no effort knob. Skills: `~/.agents/skills/`, `~/.copilot/skills/`.
- Source: github.com/github/copilot-cli, docs.github.com → copilot-cli/customize-copilot/add-skills

## Verified 2026-11-26 (Task 7)

Live acceptance of `lanes` against claude 2.1.332, codex 0.172.0, agy 1.5.18, cursor-agent 2026.11.22,
pi 1.6.0, opencode 2.3.2. Plain notes; Task 8 rewrites this file.

Orchestrator markers (env vars each harness sets in its tool shell; `detect_self_pool` uses them):
- Claude Code `CLAUDECODE=1`; Codex `CODEX_THREAD_ID` (also `CODEX_SESSION_ID`, `CODEX_CI`, `CODEX_VERSION`;
  `CODEX_SANDBOX` only while sandboxed); agy `ANTIGRAVITY_AGENT`; cursor-agent `CURSOR_AGENT`;
  pi `PI_CODING_AGENT` + `PI_PROVIDER` (also `PI_MODEL`, `PI_SESSION_ID`); opencode `OPENCODE_SESSION_ID`.
- A CLI started from another harness's shell inherits that harness's marker, so check the innermost first.
  `CODEX_COMPANION_*` is the Claude Code Codex plugin, not Codex itself. pi's pool is the one its
  `PI_PROVIDER` routes to; opencode names no provider, so its pool is unknown (pass `--from <pool>`).

Codex calling `lanes`:
- `codex exec -s workspace-write -c sandbox_workspace_write.network_access=true` runs `lanes pick` fine and
  `lanes discover` partially: Claude/Google/Cursor pools resolve, but the chatgpt quota reader fails
  (`quota_unknown`) and the pi/opencode routers come back `adapter_broken`. A full discovery needs
  `--dangerously-bypass-approvals-and-sandbox` (or an approved escalation for `lanes discover`).
  Run `lanes discover` once outside the sandbox, then let sandboxed Codex call `pick`/`run` from the cache.
- A discovery without probes takes 30-60 s; Codex's tool call gives up after 30 s unless told to wait.
- `LANES_CACHE`/`LANES_CONFIG` set on the command line reach the child.

Per-CLI headless notes:
- agy: no shell in headless mode (tool calls are denied) unless `--dangerously-skip-permissions`; file edits work
  with `--mode accept-edits`. Model slugs carry the effort (`gemini-4-neon-low|medium|high`), so the tier
  picks the slug; `--effort` is passed as well.
- cursor-agent: the Free plan lists every model but only runs Auto and Grok; others fail with
  `ActionRequiredError: Model unavailable on Free`, so the probe uses `auto`. Its tool shell re-reads the user's
  shell profile, so stripped API-key variables reappear inside it (they are not used for billing there).
- pi: `--model provider/id:effort`; its tool shell has no subagent tool and sees `PI_SUBAGENT_MAX_DEPTH=0`.
- opencode: `-m provider/model#low` works; the first listed OpenAI model may be a restricted one, so the
  probe pins `gpt-5.6-luna`. It resolves its project from `$PWD`, not the process cwd: `lanes` sets `PWD` to
  the worktree, otherwise a write job lands in the launching checkout. With the deny config its shell has no
  subagent tool.
- `lanes discover --probe` sends one prompt to every subscription pool it can see, including logins whose plan
  was cancelled (they fail cheaply). Expect about eight model calls.
- Nested guard: every child sees `LANES_DEPTH=1`; `lanes run` then exits 3.
