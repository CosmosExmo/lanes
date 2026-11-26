# Harness reference — delegation + per-agent model/effort

Contents: Claude Code · Codex CLI · OpenCode · Kimi Code · pi · Gemini CLI · Copilot CLI · This machine

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

## This machine (2026-11-25)

- Canonical skills `~/.agents/skills/` → `python3 ~/.agents/sync.py` rebuilds `~/.agents/skills-all`
  and distributes: Claude ×3 (dir symlink), Codex + Qwen + agy (per-entry mirror), Kimi
  (`extra_skill_dirs`), OpenCode (`skills.paths`), pi (skills-all root; `synced/` is ignored).
- Codex `~/.codex/config.toml`: `model = "gpt-6-astra"`, `model_reasoning_effort = "xhigh"`,
  `[features] hooks = true`, no `[agents]` block, no multi_agent_v2 override → every Codex spawn
  inherits gpt-6-astra/xhigh. To tier: add `[agents] default_subagent_model` +
  `default_subagent_reasoning_effort = "low"` and/or `expose_spawn_agent_model_overrides = true`.
- Kimi: `default_model = "kimi-code/k3"` (efforts `low|high|max`, default `high`), `[thinking]
  effort = "high"`, no `[secondary_model]` pool → subagents run k3/high.
- pi: `subagents.modelScope.allow = kimi-coding/*, openai-codex/*, xai/*` (enforced);
  roles scout→kimi-for-coding-highspeed/low, researcher→grok-4.5/medium, worker→gpt-5.6-terra/high,
  reviewer→gpt-5.6-sol/high, oracle→gpt-6-astra/xhigh, delegate→grok-4.3/low; `defaultThinking: medium`.
  Claude capacity from pi only via external `claude -p` lanes (sonnet routine, opus hard, fable critique);
  Google capacity via the `agy-gemini` lane. Cross-harness commands live in the `cross-cli-lanes` skill.
