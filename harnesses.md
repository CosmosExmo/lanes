# Harness reference — native subagents, headless lanes, orchestrating

Contents: Part 1 native subagent syntax (Claude Code · Codex · OpenCode · Kimi · pi · agy · Gemini CLI · Copilot CLI) ·
Part 2 headless notes per CLI (what `lanes run` does) · Part 3 orchestrating from each harness

Syntax in Part 1 verified 2026-11-09 against codex 0.166.0, opencode 2.2.5, kimi 2.5.2, pi 1.5.0 +
pi-subagents 0.86.0, Claude Code 2.1.319 and code.claude.com docs. Part 2 and 3 verified 2026-11-26 live
against claude 2.1.332, codex 0.172.0, agy 1.5.18, cursor-agent 2026.11.22, pi 1.6.0, opencode 2.3.2.
Gemini CLI and Copilot sections are older and unverified since. Re-check after a major version bump:
`lanes selftest` compares installed versions and required flags with `adapters.json`; grep a binary's
strings or bundled docs rather than trusting this file.

## Part 1 — Native subagent syntax (your own pool)

### Claude Code

- `Agent` tool: `subagent_type`, `model: haiku|sonnet|opus|fable` (aliases only, no full IDs or
  `inherit`), `effort: low|medium|high|xhigh|max` (v2.1.292+), `run_in_background`, `isolation: worktree`.
  `subagent_type: "fork"` = full context + session model + shared prompt cache.
- Model resolution: call param → agent file `model` → `CLAUDE_CODE_SUBAGENT_MODEL` → session model.
  A family alias equal to the session's family resolves to the session's exact model.
  Built-in `Explore`/`Plan` run on the session model (from Fable: on Opus) unless `model` is passed.
  Optional safety net: `CLAUDE_CODE_SUBAGENT_MODEL=sonnet` in settings `env` catches omitted models
  (unset by default; doesn't affect Explore/Plan or forks).
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

### OpenAI Codex CLI (0.166)

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

### OpenCode (2.2.5)

- Subagent = agent with `mode: "subagent"` (`"primary"|"all"` also valid) in
  `~/.config/opencode/opencode.jsonc` → `agent.<name>.{description, model, permission, steps, hidden}`.
- Model: `"provider/model#variant"` or `{providerID, model, variant}`; effort comes from the variant,
  defined at `provider.<p>.models.<m>.variants.<id>.settings` (e.g. a reasoning-effort option).
  The spawn tool also takes `model`, but its text says not to set it unless asked — prefer pinning
  `model` on cheap dedicated subagent definitions.
- Source: `strings $(brew --prefix)/Cellar/opencode/*/bin/opencode` (Config.Agent schema),
  https://opencode.ai/docs/agents , /docs/models

### Kimi Code (2.5.2)

- Tools: `Agent` (`description`, `subagent_type`, `fork`, `model`, `resume`, background) and
  `AgentSwarm` (batch spawn, one shared `model`). Background results are fed back automatically.
- `model` must be an alias from the `[secondary_model]` pool; with no pool, subagents use
  `default_model`. No per-call effort: it comes from `[models."<alias>"] default_effort`
  (`support_efforts`) or global `[thinking] effort`. `secondary_model.force` is obsolete.
- Source: `@moonshot-ai/kimi-code/dist/main.mjs` (Agent/AgentSwarm zod schema),
  https://moonshotai.github.io/kimi-code/en/configuration/config-files.html

### pi (1.5.0, pi-subagents 0.86.0)

- Tool `subagent` (enable with `subagents_enable({})`; `/run <role>[model=provider/id:level] "task"`).
  Per-launch `model` (exact `provider/id`, optional `:thinking` suffix) and `thinking` params.
- Precedence: per-run override → `agentOverridesByProvider.<provider>.<role>` →
  `agentOverrides.<role>.{model,thinking,fast}` → agent frontmatter → `defaultModel`/parent.
- `modelScope {enforce, allow}` only rejects out-of-scope models; it never picks a cheaper one.
- One model per launch, no fallback chains: if it fails, relaunch explicitly with another in-scope
  model. `fast: true` only for interactive loops (burns quota).

### Antigravity CLI — `agy` (1.5.16)

- Subagents: `~/.gemini/config/agents/<name>.md` (workspace `.agents/agents/`), frontmatter `name`,
  `description`, `tools`, `model` (`inherit|flash|pro|flash_lite`); `agy --agent <name>`, `agy agents`.
- Skills: does NOT read `~/.agents/skills`; mirror or link skills into `~/.gemini/antigravity-cli/skills`.
  The server-side customization budget can exclude some skills when there are many (listed by name only).
- Source: antigravity.google/docs (cli/reference, cli/headless, subagents, install).

### Gemini CLI (2026-11-25)

- No longer serves free, AI Pro or Ultra accounts since 2026-06-18 (`UNSUPPORTED_CLIENT`); only API key,
  Vertex or Code Assist Standard/Enterprise. Google subscription capacity is reached through `agy`.

### GitHub Copilot CLI (unverified since 2026-08-26)

- Custom agents JSON `agents: [{name, entrypoint, model, tools}]`, `/agent` or `--agent <name>`;
  no in-session spawn tool, no effort knob. Skills: `~/.agents/skills/`, `~/.copilot/skills/`.
- Source: github.com/github/copilot-cli, docs.github.com → copilot-cli/customize-copilot/add-skills

## Part 2 — Headless notes per CLI (the lanes `lanes run` starts)

All lanes: stdin is `/dev/null` (claude reads the prompt file on stdin), API-key variables
(`OPENAI_API_KEY`, `CODEX_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `CURSOR_API_KEY`, ...) are stripped
from the child (every known provider key: also xAI, DeepSeek, Z.ai/Zhipu, DashScope, OpenRouter, Groq, Mistral and the
Anthropic token variables; only qwen keeps the Bailian coding-plan keys, which are subscription keys) so a
subscription lane cannot fall through to API billing, stderr is noisy (banners, MCP
errors; the answer is in `out.txt`), and the child sees `LANES_DEPTH=1`, so `lanes run` inside it exits 3.
A write job is judged by `git diff`, never by exit code. Pools and billing come from `lanes discover`;
a login alone is not a plan (`logged_in_unverified` is not used until quota or `--probe` shows a plan).
`lanes discover --probe` sends one prompt to every subscription pool it can see, including logins whose
plan was cancelled (they fail cheaply); expect about eight model calls, so run it only when asked.

| CLI | Quota source (headless) | Windows | Run template | Billing trap |
|---|---|---|---|---|
| claude | `claude -p "/usage" --output-format json`, 0 tokens | 5h, weekly, per-model weekly | `claude -p --model M --effort E --permission-mode plan\|acceptEdits --output-format json` | API key in env |
| codex | `codex app-server` JSON-RPC `account/rateLimits/read` | per account (often weekly only) | `codex exec -m M -c model_reasoning_effort=E -s read-only\|workspace-write --ephemeral -o out.txt "<p>" </dev/null` | credit balance spendable after the limit |
| agy | `agy -p "/usage" --output-format json`, 0 tokens | 5h + weekly per model group (Gemini; Claude and GPT) | `agy -p "<p>" --model M --effort E --mode plan\|accept-edits --output-format json </dev/null`; read adds `--settings '{"permissions":{"allow":["read_file(<cwd>)"]}}'` | AI Credits when `useG1Credits` is on; `modelProvider: "gemini"` + key = API |
| cursor-agent | none official; internal usage API needs the keychain token, so off by default (`cursor_read_quota`) | monthly | `cursor-agent -p --trust --model M-effort --mode ask`, `--force` to write | on-demand billing on paid plans |
| pi | none of its own; read from the pool's official CLI | n/a | `pi -p --no-session --no-extensions --no-skills --no-context-files --no-mcp --model provider/id:effort`, read adds `--tools read,grep,find,ls` | an `openai` provider (API key) sits next to `openai-codex` (subscription) |
| opencode | none (`stats` is local counting) | n/a | `opencode run --standalone --archived --agent plan\|build -m 'provider/model#variant'` with permissions in `OPENCODE_CONFIG_CONTENT` | without `--standalone` the shared background service uses its own env, API keys included |
| kimi, qwen | unverified: first `--probe` on your machine decides; limits tracked reactively from error text | n/a | see `adapters.json` | qwen: `OPENAI_API_KEY`+`OPENAI_BASE_URL`+`OPENAI_MODEL` together silently switch to paid openai-compatible mode |

Multi-provider CLIs (pi, opencode, qwen) are a second door into pools, not pools. Credentials decide billing:
pi uses `pi auth check` `authType` (`oauth` → the mapped subscription pool; anything else → `api:<provider>`);
opencode counts a provider as the subscription only when its remaining connections (after the key strip) are OAuth,
or it is a mapped coding plan with a stored credential, and OAuth next to a stored API key is `unknown` billing.
Provider → pool and billing mapping lives in `adapters.json` (for example pi `openai-codex` and opencode `openai` OAuth → `chatgpt`;
`deepseek`, `moonshotai` and pi `openai` → `api`). A pool's quota is read from its official CLI when installed
(ChatGPT → codex RPC), otherwise tracked reactively: a limit error marks the pool `exhausted until reset`.
The Anthropic pool is reached only through `claude`; Anthropic logins inside pi/opencode are ignored.

Per-CLI notes:
- **claude**: the nesting guard is `--disallowedTools Agent`.
- **codex**: model slugs and effort levels come from `codex debug models`.
- **agy**: no shell in headless mode (tool calls are denied, exit code still 0, JSON `denied_actions` lists them)
  unless `--dangerously-skip-permissions`, which read mode never uses. Read mode grants read-only access for the
  session with `--settings '{"permissions":{"allow":["read_file(<cwd>)"]}}'` (verified live on 1.5.18; the key is
  `permissions`, `permission` is rejected as an unknown setting; rule names are `read_file(<path>)`,
  `write_file(<path>)`, `command(<prefix>)`, `mcp(<server>/<tool>)`, `read_url(<domain>)`). Without that grant a
  read job returns `"response": ""` and exit 0; `lanes` marks an empty answer `failed` ("empty answer") and notes
  "tools denied: <actions>". File edits work with `--mode accept-edits`. Model slugs carry the effort
  (`<model>-low|medium|high`), so the tier picks the slug; `--effort` is passed as well. Every call costs a
  ~26k-token input floor, so tiny jobs are not worth a lane. Jobs that need to run tests (`--needs-shell`) skip agy
  unless the `agy_allow_shell` config key is set.
- **cursor-agent**: the Free plan lists every model but only runs Auto and Grok (`ActionRequiredError: Model
  unavailable on Free`), so the probe uses `auto`. About 10 s start-up. Its tool shell re-reads the user's shell
  profile, so stripped API-key variables reappear inside it (they are not used for billing there).
- **pi**: `--model provider/id:effort`; its tool shell has no subagent tool and sees `PI_SUBAGENT_MAX_DEPTH=0`.
  Always pass the full `openai-codex/...` model, never a bare `openai/...`.
- **opencode**: `-m provider/model#low` works; the first listed OpenAI model may be a restricted one, so the
  probe pins `gpt-5.6-luna`. It resolves its project from `$PWD`, not the process cwd: `lanes` sets `PWD` to
  the worktree, otherwise a write job lands in the launching checkout. With the deny config its shell has no
  subagent tool.
- **Nesting guard** per CLI: claude `--disallowedTools Agent`, pi `--no-extensions` + `PI_SUBAGENT_MAX_DEPTH=0`,
  opencode `subagent` deny in the injected config, qwen `--exclude-tools agent`; every lane prompt also opens
  with a "do not delegate" preamble.

## Part 3 — Orchestrating from each harness

`lanes` infers the caller's own pool from env vars the harness sets in its tool shell; a pool equal to the
caller's is refused by `lanes run` (use a native subagent), and `pick` marks it `native`.

| Harness | Marker | Own pool |
|---|---|---|
| Claude Code | `CLAUDECODE=1` | `anthropic` |
| Codex | `CODEX_THREAD_ID` (also `CODEX_SESSION_ID`, `CODEX_CI`, `CODEX_VERSION`; `CODEX_SANDBOX` only while sandboxed) | `chatgpt` |
| agy | `ANTIGRAVITY_AGENT` | `google:claude-gpt` when `model` in `~/.gemini/antigravity-cli/settings.json` starts with `claude-` or `gpt-`, else `google:gemini` |
| cursor-agent | `CURSOR_AGENT` | `cursor` |
| pi | `PI_CODING_AGENT` + `PI_PROVIDER` (also `PI_MODEL`, `PI_SESSION_ID`) | the pool its `PI_PROVIDER` routes to |
| opencode | `OPENCODE_SESSION_ID` | unknown: it names no provider, pass `--from <pool>` |
| Kimi, Qwen | none verified | pass `--from <pool>` |
| none of the above | - | pass `--from <pool>`, or `--from none` when you are in no pool |

- A CLI started from another harness's shell inherits that harness's marker, so the innermost one wins.
  `CODEX_COMPANION_*` belongs to a Claude Code Codex plugin, not to Codex.
- When no marker is recognised `run` exits 2 ("cannot tell your own pool; pass --from <pool> or --from none") and
  `pick` ranks without a `native` row; `--from <pool>|none` is accepted by both. `--allow-self` overrides the
  own-pool refusal.
- `LANES_CACHE` (default `~/.cache/lanes`) and `LANES_CONFIG` (default `~/.config/lanes/config.json`) set on the
  command line reach the child. Config keys: `cursor_read_quota`, `agy_allow_shell`, `orchestrator_reserve_pct`.

**Codex as orchestrator (sandbox).** `codex exec -s workspace-write -c sandbox_workspace_write.network_access=true`
runs `lanes pick` fine and `lanes discover` partially: Claude, Google and Cursor pools resolve, but the ChatGPT quota
reader fails (`quota_unknown`) and the pi/opencode routers come back `adapter_broken`. A full discovery needs
`--dangerously-bypass-approvals-and-sandbox` or an approved escalation for `lanes discover`. Run `lanes discover`
once outside the sandbox, then let the sandboxed session call `pick` and `run` from the cache. A discovery without
probes takes 30-60 s and Codex's tool call gives up after 30 s unless told to wait.

**Claude Code, agy, Cursor, pi, OpenCode, Kimi, Qwen as orchestrator:** no sandbox caveat found. Call `lanes`
through the shell tool; write lanes need the working directory to be a git repo with a commit.
