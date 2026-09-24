# Configuration Reference

Applies to: 1.8.1 source tree · Updated: 2026-09-21

[Documentation](README.md) · [中文](configuration.md) · [Operations](ops-manual_en.md)

**The main database is the persistent settings source.** Model profiles live in `model_profiles`; other settings live in `config_kv`. The default database is `~/.excelmanus/excelmanus.db`. Settings, configuration import, and `/config` share this store. An in-process overlay can supply effective runtime values; `load_config()` does not read product settings directly from environment variables.

After launch, add a model in Web Settings; Settings remains available before a model is configured; save and activate a valid profile to begin a conversation.

The process may use a few **locators** to find the data volume and bind ports. Start scripts / systemd set these. They are **not** a settings overlay:

| Locator | Description | Default |
|---|---|---|
| `EXCELMANUS_HOME` | Durable home (main database, encryption keys) | `~/.excelmanus` |
| `EXCELMANUS_DB_PATH` | Main database path | `{EXCELMANUS_HOME}/excelmanus.db` |
| `EXCELMANUS_DATA_ROOT` | Centralized data directory (uploads / outputs; secrets are not stored here) | `{EXCELMANUS_HOME}/data` |
| `EXCELMANUS_DEPLOY_MODE` | `auto`/`standalone`/`server`. `auto` and unknown values are standalone; `server` must be set explicitly | `auto` |
| `EXCELMANUS_API_HOST` / `EXCELMANUS_API_PORT` / `EXCELMANUS_BACKEND_PORT` / `EXCELMANUS_FRONTEND_PORT` | Bind address and ports | See start scripts |
| `EXCELMANUS_WEB_WORKERS` | uvicorn worker count; API warns about process-local session and cache state when `>1`. Keep `1` on a single host | Set by `deploy/start.*`, default `1` |
| `EXCELMANUS_EXECUTION_ISOLATION` | `local` (default) or `docker`; moves `run_code` into a short-lived container with resource and network limits | `local` |
| `EXCELMANUS_DOCKER_IMAGE` | Image used by Docker code execution; it must include the required spreadsheet runtime | `excelmanus/runtime:latest` |
| `EXCELMANUS_MANAGE_TOKEN` | Optional automation/desktop token, at least 16 characters; accepts `Authorization: Bearer` or `X-ExcelManus-Token`, not URL query parameters | empty |
| `EXCELMANUS_LOGIN_USERNAME` | Initial administrator username; Settings → Security takes precedence | `admin` |
| `EXCELMANUS_LOGIN_PASSWORD` | Initial administrator password, at least 12 characters; Settings → Security takes precedence | empty (local protection off by default) |
| `EXCELMANUS_LOGIN_SESSION_HOURS` | Browser session lifetime, integer 1–168 hours | `12` |
| `EXCELMANUS_LOGIN_COOKIE_SECURE` | `auto` follows the request HTTPS scheme; HTTPS reverse proxies can set `true` explicitly | `auto` |
| `EXCELMANUS_SECRET_KEY` | Fernet key seed (tests / custom volumes) | empty → `{EXCELMANUS_HOME}/.secret_key` |
| `EXCELMANUS_DESKTOP` | Desktop marker, set by the desktop launcher | Unset for source launches |
| `EXCELMANUS_RUN_PYTHON` | Python executable for `run_code`; desktop sets its bundled runtime | Depends on the runtime |
| `EXCELMANUS_WEB_UPGRADE_ENABLED` | Allow one-click web upgrades on `server` mode or non-loopback access; also requires login protection and administrator authentication | empty (server web upgrade off) |

Do not put model secrets or runtime options in the process environment; leftover product keys are ignored and logged.

Except for entries marked as locators, the names below are database setting keys. Prefer the settings UI; some advanced keys may not have individual controls. The `EXCELMANUS_` prefix does not make them environment overrides. Apply changes or restart when prompted. Bind ports and data locations must be supplied by the launching process.

Model-profile API keys are encrypted in the main database. The Fernet key lives at `$EXCELMANUS_HOME/.secret_key` (it does not follow DATA_ROOT, so it cannot fall into an Agent workspace). Preserve the matching key when moving or restoring the database; copying only the database can make credentials unreadable.

## Basic Configuration

| Setting key | Description | Default |
|---|---|---|
| `EXCELMANUS_API_KEY` | `config_kv` fallback when no active profile; add a profile in Settings | — |
| `EXCELMANUS_BASE_URL` | `config_kv` fallback when no active profile | — |
| `EXCELMANUS_MODEL` | `config_kv` fallback when no active profile; Gemini can be auto-extracted from BASE_URL | — |
| `EXCELMANUS_PROTOCOL` | Model protocol type (`auto`/`openai`/`openai_responses`/`anthropic`/`gemini`) | `auto` |
| `EXCELMANUS_MAX_ITERATIONS` | Per-turn cap on LLM rounds and tool calls; `0` means unlimited | `0` |
| `EXCELMANUS_TURN_TIMEOUT_SECONDS` | Wall-clock limit for one turn (`0` disables the limit) | `0` |
| `EXCELMANUS_RESPONSES_CONTINUATION_ENABLED` | Enable native Responses API `previous_response_id` continuation | `false` |
| `EXCELMANUS_RESPONSES_BACKGROUND_ENABLED` | Use Responses API background responses and poll to a terminal state | `false` |
| `EXCELMANUS_TURN_TOKEN_BUDGET` | Maximum input plus output tokens for one turn (`0` disables the limit) | `0` |
| `EXCELMANUS_TURN_COST_BUDGET_USD` | Maximum cost for one turn in USD (`0` disables the limit) | `0` |
| `EXCELMANUS_INPUT_COST_PER_1K_USD` | Estimated input-token price when the provider omits cost metadata | `0` |
| `EXCELMANUS_OUTPUT_COST_PER_1K_USD` | Estimated output-token price when the provider omits cost metadata | `0` |
| `EXCELMANUS_MAX_CONSECUTIVE_FAILURES` | Consecutive failure circuit-breaker threshold | `6` |
| `EXCELMANUS_SESSION_TTL_SECONDS` | API session idle timeout (seconds) | `1800` |
| `EXCELMANUS_MAX_SESSIONS` | Maximum concurrent API sessions | `1000` |
| `EXCELMANUS_WORKSPACE_ROOT` | File access whitelist root directory | `~/.excelmanus/data` |
| `EXCELMANUS_LOG_LEVEL` | Log level | `INFO` |
| `EXCELMANUS_CORS_ALLOW_ORIGINS` | API CORS allowed origins (comma-separated). Runtime also adds `localhost` / `127.0.0.1` / `[::1]` plus the frontend port | `http://localhost:3000,http://127.0.0.1:3000` |
| `EXCELMANUS_MAX_CONTEXT_TOKENS` | Explicit override for the inferred model context limit | Inferred from the model; `256000` fallback |
| `EXCELMANUS_PROMPT_CACHE_KEY_ENABLED` | Send prompt_cache_key to API to improve cache hit rate | `true` |
| `EXCELMANUS_PROMPT_CACHE_RETENTION` | Prompt cache retention policy (`default`/`extended`). `extended` applies only to first-party endpoints: Anthropic (`api.anthropic.com`) adds `ttl=1h` to every `cache_control` breakpoint and sends `anthropic-beta: extended-cache-ttl-2025-04-11`; OpenAI (`api.openai.com`, Chat and Responses) sends top-level `prompt_cache_retention=24h`. Compatible gateways and self-hosted endpoints never receive these fields | `default` |

## Skillpack & Routing Configuration

| Setting key | Description | Default |
|---|---|---|
| `EXCELMANUS_SKILLS_SYSTEM_DIR` | Built-in Skillpacks directory | `excelmanus/skillpacks/system` |
| `EXCELMANUS_SKILLS_USER_DIR` | User-level Skillpacks directory | `~/.excelmanus/skillpacks` |
| `EXCELMANUS_SKILLS_PROJECT_DIR` | Project-level Skillpacks directory | `<workspace_root>/.excelmanus/skillpacks` |
| `EXCELMANUS_SKILLS_CONTEXT_CHAR_BUDGET` | Skill body character budget (0 = unlimited) | `12000` |
| `EXCELMANUS_SKILLS_DISCOVERY_ENABLED` | Enable general directory discovery | `true` |
| `EXCELMANUS_SKILLS_DISCOVERY_SCAN_WORKSPACE_ANCESTORS` | Scan cwd→workspace ancestor chain for `.agents/skills` | `true` |
| `EXCELMANUS_SKILLS_DISCOVERY_INCLUDE_AGENTS` | Discover `.agents/skills` | `true` |
| `EXCELMANUS_SKILLS_DISCOVERY_SCAN_EXTERNAL_TOOL_DIRS` | Discover external tool directories | `true` |
| `EXCELMANUS_SKILLS_DISCOVERY_EXTRA_DIRS` | Extra scan directories (comma-separated) | empty |
| `EXCELMANUS_TOOL_RESULT_HARD_CAP_CHARS` | Tool result global hard truncation length (0 = unlimited) | `12000` |

## Subagent Configuration

| Setting key | Description | Default |
|---|---|---|
| `EXCELMANUS_SUBAGENT_ENABLED` | Enable subagent execution | `true` |
| `EXCELMANUS_SUBAGENT_MAX_ITERATIONS` | Child-loop cap on LLM rounds and tool calls; `0` means unlimited | `0` |
| `EXCELMANUS_SUBAGENT_MAX_CONSECUTIVE_FAILURES` | Subagent consecutive failure circuit-breaker threshold | `6` |
| `EXCELMANUS_SUBAGENT_TIMEOUT_SECONDS` | Single subagent execution timeout (seconds) | `600` |
| `EXCELMANUS_PARALLEL_SUBAGENT_MAX` | Synchronous batch limit and per-session background concurrency; excess background runs queue | `3` |
| `EXCELMANUS_PARALLEL_READONLY_TOOLS` | Run independent read-only tools concurrently; dependencies are checked even when disabled | `true` |
| `EXCELMANUS_PARALLEL_TOOL_MAX` | Maximum active read-only calls per batch, 1–32; applies to new sessions | `4` |
| `EXCELMANUS_SUBAGENT_USER_DIR` | User-level subagent directory | `~/.excelmanus/agents` |
| `EXCELMANUS_SUBAGENT_PROJECT_DIR` | Project-level subagent directory | `<workspace_root>/.excelmanus/agents` |

`delegate` waits by default. A single task with `background=true` returns `run.run_id`
immediately. Use `action=status/list/wait` to retrieve results, `send` to steer a running
task or answer its question, `pause/cancel` to stop it, and `resume` to start a new run
with the saved conversation. Resume returns a new ID. `wait_seconds` ranges from 0 to
60; a query timeout does not cancel the task.

Runs and results use session snapshots. On restart, unfinished runs become
`interrupted`; explicit resume continues from the saved conversation, not from an old
process, thread or external request. Pause/cancel waits for an in-flight local synchronous
call to finish and preserves committed changes. Execution timeout starts after queueing.
Finishing the main chat leaves background runs alive; deleting the session or shutting
down the service stops them.

Use `GET /api/v1/sessions/{session_id}/subagents` and
`POST /api/v1/sessions/{session_id}/subagents/{run_id}` with `action` and optional
`message` / `wait_seconds`. `GET /api/v1/sessions/{session_id}/task-list` returns
the session's current task list snapshot (`null` when none exists). The Tasks
button in the chat toolbar shows the assistant's task list progress plus
background runs, results, and changed files. It supports steering, question
answers, pause, cancel, and continuation. Active background runs in the
selected session are polled every two seconds, even after the main chat ends or
is stopped. Returning to a session or reloading the page fetches its records
again. Continue creates a new run and keeps the previous record. Settled runs
refresh their changed file views. The panel shows the selected session; tasks
are started through `delegate` in chat and are not automatically resumed when
the page loads.

## Agent self-management

Enabled by default; you can disable it under Settings → System → Capabilities. While enabled, the `agent_self_management` skill is available: `inspect_agent` reports capabilities and settings, and `configure_agent` adjusts reasoning, context, and tool switches for the current session. Saving the switch applies to live sessions immediately. Changes stay in the in-memory session; credentials, approval permissions, and global defaults cannot be modified.

| Setting key | Description | Default |
|---|---|---|
| `EXCELMANUS_AGENT_SELF_MANAGEMENT_ENABLED` | Enable the self-management skill and its `inspect_agent` / `configure_agent` tools | `true` |

## Context Auto-Compaction

As context approaches the threshold, the active model summarizes earlier dialogue while retaining recent content. Compaction adds model requests. Context overflow or recovery may require the current request to wait for compaction or retry.

| Setting key | Description | Default |
|---|---|---|
| `EXCELMANUS_COMPACTION_ENABLED` | Enable auto-compaction | `true` |
| `EXCELMANUS_COMPACTION_THRESHOLD_RATIO` | Context ratio threshold to trigger compaction | `0.85` |
| `EXCELMANUS_COMPACTION_KEEP_RECENT_TURNS` | Number of recent turns to keep during compaction | `5` |
| `EXCELMANUS_COMPACTION_MAX_SUMMARY_TOKENS` | Generation budget for a compaction summary | `4096` |

## Hook Configuration

| Setting key | Description | Default |
|---|---|---|
| `EXCELMANUS_HOOKS_COMMAND_ENABLED` | Allow `command` hook execution | `false` |
| `EXCELMANUS_HOOKS_COMMAND_ALLOWLIST` | `command` hook allowlist prefixes (comma-separated) | empty |
| `EXCELMANUS_HOOKS_COMMAND_TIMEOUT_SECONDS` | `command` hook timeout (seconds) | `10` |
| `EXCELMANUS_HOOKS_OUTPUT_MAX_CHARS` | Hook output truncation length | `32000` |

## Tools and permissions

- `write` permits operations within the current workspace permissions. Direct tools and `run_code` can be used in the same task.
- `read` and `plan` hide pure-write tools. Tools with read-only actions remain discoverable, while their write actions are blocked at execution. For example, listing revisions is allowed; checkpoint, restore, and delete actions still require write permission.
- Common spreadsheet tools and essential controls load upfront. Objects, versions, formula tracing, Word, file operations, delegation, and MCP load on demand through `introspect_capability`. The `em.*` SDK binds the complete authorized execution catalog.
- Skills load through `skill`, `/<skill_name>`, or `@` without changing permissions. Approval policy and read-only restrictions are separate: ordinary edits may proceed without a dialog, while read-only restrictions still apply.
- With approval set to **Skip**, the host automatically approves model tool and shell requests. `run_code` and shell commands may access the network, start child processes, and execute commands outside the restricted allowlist. This mode permits local command execution and should be enabled only for trusted tasks. **Ask** keeps the existing allowlist, network, and approval restrictions.

See [prompt maintenance](prompt-layering.md) and the [Skillpack protocol](skillpack_protocol_en.md) for implementation details.

## Multi-Model

> **Note**: `EXCELMANUS_MODELS` is removed. Model profiles live only in the main database via the Web settings page.

Project `.env` and user `config.env` files are not product configuration sources and are not imported automatically.
The bench tool still offers an explicit, one-time `python -m excelmanus.bench --import-env PATH` command.
It writes profiles to the database; it is not part of startup configuration loading and does not generate source-file descriptions.

- Only one model is active. `/model <name>` switches the active profile.
- Chat, subagents, compaction, and memory extraction all use that active model.
- Provider presets, default model IDs, protocols, thinking modes, model families, and logos have one source of truth in `web/src/components/settings/model/constants.tsx`. The onboarding guide derives its connection fields from those presets and keeps only explanatory copy.
- Provider presets supply connection defaults; they do not guarantee access to a listed model. Use the provider model list, connection tests, and capability probes to check availability.
- Prefer the profile protocol `openai_responses` for a Responses endpoint. Vision, tool calling, and reasoning controls still depend on the model and gateway.

### Codex subscription connection

Connect under Settings → Model → Subscription account. The browser callback in this integration is fixed to `http://localhost:1455/auth/callback`. For remote deployments, use a device code or paste the full callback URL as prompted. Do not replace it with your deployment domain. OAuth credentials are encrypted in the main database as process-level configuration; this does not create an ExcelManus user account.

## Model capability probes

Connection and capability probes can be started in model settings. They call the configured endpoint and may incur charges. Results depend on the provider, gateway, and current availability. These advanced options are database keys, not environment variables:

| Setting key | Description | Default |
| --- | --- | --- |
| `CAP_PROBE_JOB_CONCURRENCY` | Global probe concurrency | `2` |
| `CAP_PROBE_PROVIDER_CONCURRENCY` | Per-provider probe concurrency | `1` |
| `CAP_PROBE_HEALTH_TIMEOUT` | Connection probe timeout, seconds | `8` |
| `CAP_PROBE_TOOL_TIMEOUT` | Tool-call probe timeout, seconds | `20` |
| `CAP_PROBE_VISION_TIMEOUT` | Vision probe timeout, seconds | `20` |
| `CAP_PROBE_THINKING_TOTAL_TIMEOUT` | Total reasoning-probe budget, seconds | `30` |
| `CAP_PROBE_THINKING_STRATEGY_TIMEOUT` | Per-strategy reasoning timeout, seconds | `8` |

## Vision

Images go to the active model only. If it has no vision, attachments are rejected. If it does, `read_image` or workbench attachments inject the picture; the model writes a `WorkbookSpec` and calls `apply_spreadsheet_changes(workbook_spec=)`. There is no separate vision pipeline and no auxiliary VLM description.

| Setting key | Description | Default |
|---|---|---|
| `EXCELMANUS_MAIN_MODEL_VISION` | Active model vision capability (`auto`/`true`/`false`) | `auto` |
| `EXCELMANUS_IMAGE_PIXEL_BUDGET` | Request-image pixel budget (integer or `low`) | `640000` |
| `EXCELMANUS_IMAGE_MAX_BYTES` | Encoded request-image byte cap | `1048576` |
| `EXCELMANUS_IMAGE_FILES_API` | Files transport: `auto` / `true` / `false` | `auto` |
| `EXCELMANUS_FRIENDLY_ERROR_MESSAGES` | Map HTTP internals to readable errors | `true` |
| `EXCELMANUS_LLM_RETRY_MAX_ATTEMPTS` | Max LLM attempts including the first | `3` |
| `EXCELMANUS_LLM_RETRY_BASE_DELAY_SECONDS` | Exponential backoff base delay (seconds) | `2.0` |
| `EXCELMANUS_LLM_RETRY_MAX_DELAY_SECONDS` | Max per-retry wait (seconds) | `30.0` |

## File revisions and legacy backup migration

Backup overlay is removed. Writes land on the user path; history lives in `.excelmanus/revisions/`.

The first workspace open after upgrade imports leftover `outputs/backups` into RevisionStore once; the marker is `.excelmanus/migrations/overlay-backups.json`. To re-run:

```bash
uv run python -m excelmanus.workspace.migrate /path/to/workspace --force
```

## Code Policy Engine Configuration

Performs static analysis on code executed by `run_code`, automatically routing approval by security level.

| Setting key | Description | Default |
|---|---|---|
| `EXCELMANUS_CODE_POLICY_ENABLED` | Enable code policy engine | `true` |
| `EXCELMANUS_CODE_POLICY_GREEN_AUTO` | Auto-approve Green-level (safe) code | `true` |
| `EXCELMANUS_CODE_POLICY_YELLOW_AUTO` | Auto-approve Yellow code with NETWORK capability; ordinary file writes follow workspace and version rules without per-write confirmation through this switch | `false` |
| `EXCELMANUS_CODE_POLICY_EXTRA_SAFE` | Extra safe module allowlist (comma-separated) | empty |
| `EXCELMANUS_CODE_POLICY_EXTRA_BLOCKED` | Extra blocked module blocklist (comma-separated) | empty |

## Persistent Memory

| Setting key | Description | Default |
|---|---|---|
| `EXCELMANUS_MEMORY_ENABLED` | Global memory switch | `true` |
| `EXCELMANUS_MEMORY_DIR` | Memory directory | `~/.excelmanus/memory` |
| `EXCELMANUS_MEMORY_AUTO_LOAD_LINES` | `load_core` cap; not injected at session start | `200` |
| `EXCELMANUS_MEMORY_EXPIRE_DAYS` | Expire memories on host session start; `0` disables | `90` |
| `EXCELMANUS_MEMORY_MAINTENANCE_ENABLED` | LLM maintenance after new extractions | `false` |
| `EXCELMANUS_MEMORY_MAINTENANCE_MIN_ENTRIES` | Skip maintenance below this count | `10` |
| `EXCELMANUS_MEMORY_MAINTENANCE_NEW_THRESHOLD` | New entries required before maintenance | `5` |
| `EXCELMANUS_MEMORY_MAINTENANCE_INTERVAL_HOURS` | Minimum hours between maintenance runs | `4.0` |
| `EXCELMANUS_MEMORY_MAINTENANCE_MODEL` | Maintenance model ID; empty uses the active model | empty |

Topic files: `file_patterns.md`, `user_prefs.md`, `error_solutions.md`, `general.md`.
Memory is read on demand via tools; it is not injected into the prompt at session start. Dedup uses `UNIQUE(category, content_hash)`.

## Built-in Search Engines

Three search engine MCP Servers are built-in (Exa / Tavily / Brave), requiring no manual `mcp.json` configuration.

**Enabling strategy** ("default + optional" mode):
1. `EXCELMANUS_EXA_SEARCH=false` → disables all built-in search engines
2. The engine specified by `EXCELMANUS_SEARCH_DEFAULT` is always enabled
3. Non-default engines are additionally enabled only when their API key is configured
4. If the default engine is tavily/brave but lacks an API key or Node.js → auto-fallback to exa

Tavily and Brave are launched via `npx` (stdio transport) and require Node.js. Exa connects via HTTP and does not require Node.js.

| Setting key | Description | Default |
|---|---|---|
| `EXCELMANUS_EXA_SEARCH` | Master switch for built-in search engines | `true` |
| `EXCELMANUS_SEARCH_DEFAULT` | Default search engine (`exa` / `tavily` / `brave`) | `exa` |
| `EXCELMANUS_EXA_API_KEY` | Exa API key (optional, improves search quality and rate limits) | — |
| `EXCELMANUS_TAVILY_API_KEY` | Tavily API key (enables Tavily search when configured) | — |
| `EXCELMANUS_BRAVE_API_KEY` | Brave API key (enables Brave search when configured) | — |

Users can override built-in configurations by defining a server with the same name (e.g., `"exa"`) in `mcp.json`.

## MCP Configuration

Custom MCP servers go in the workspace or `~/.excelmanus/mcp.json` (or `EXCELMANUS_MCP_CONFIG`):

- `stdio` entries start via `command`/`args`; Tavily / Brave use `npx` and need Node.js.
- Exa uses HTTP (`streamable_http` or SSE) and does not need `npx`.
- Process state defaults to `<workspace>/.excelmanus/mcp`; override with `EXCELMANUS_MCP_STATE_DIR`.

| Setting key | Description | Default |
|---|---|---|
| `EXCELMANUS_MCP_CONFIG` | Custom MCP configuration path, before default search locations | empty |
| `EXCELMANUS_MCP_STATE_DIR` | MCP process state directory | `<workspace>/.excelmanus/mcp` |
| `EXCELMANUS_MCP_EXPAND_ENV_REFS` | Expand `$VAR` / `${VAR}` references in MCP configuration | `true` |
| `EXCELMANUS_MCP_SHARED_MANAGER` | Whether API sessions reuse a shared MCP manager | `false` |
| `EXCELMANUS_MCP_ENABLE_STREAMABLE_HTTP` | Enable streamable_http transport | `true` |
| `EXCELMANUS_MCP_UNDEFINED_ENV` | Undefined environment variable policy (`keep`/`empty`/`error`) | `keep` |
| `EXCELMANUS_MCP_STRICT_SECRETS` | Block loading on plaintext sensitive fields | `false` |

`mcp.json` capabilities:
- `transport` supports `stdio`, `sse`, `streamable_http`.
- Supports `$VAR` / `${VAR}` in `args/env/url/headers`. That expands **the MCP child process environment** (secrets for that server), not the product settings store.
- MCP registers `mcp_*` tools; runtime policy controls visibility and execution permissions. Skills can document their use and declare `required-mcp-servers` / `required-mcp-tools`, but cannot grant permissions.

MCP security scanning:
- Local: `scripts/security/scan_secrets.sh`
- pre-commit: Built-in hook in `.pre-commit-config.yaml`
- CI: `.github/workflows/security-secrets.yml`

## Unified Database

| Setting key | Description | Default |
|---|---|---|
| `EXCELMANUS_DB_PATH` | SQLite path (chat history, memory, approvals, model profiles, and user settings) | `~/.excelmanus/excelmanus.db` |

## Chat History Persistence

| Setting key | Description | Default |
|---|---|---|
| `EXCELMANUS_CHAT_HISTORY_ENABLED` | Enable chat history persistence | `true` |

## Session summary

Off by default. When enabled, a summary is written to the database at session end only — no retrieval and no injection into new sessions.

| Setting key | Description | Default |
|---|---|---|
| `EXCELMANUS_SESSION_SUMMARY_ENABLED` | Write a session-end summary to the database | `false` |
| `EXCELMANUS_SESSION_SUMMARY_MIN_TURNS` | Skip summary below this turn count | `3` |

## API pool

Off by default. When enabled, Settings can manage an API credential pool and subscription rotation.

| Setting key | Description | Default |
|---|---|---|
| `EXCELMANUS_POOL_ENABLED` | Enable the credential pool | `false` |
| `EXCELMANUS_POOL_AUTO_ENABLED` | Enable automatic rotation | `false` |
| `EXCELMANUS_POOL_AUTO_INTERVAL` | Auto-rotation check interval (seconds) | `60` |
| `EXCELMANUS_POOL_AUTO_COOLDOWN` | Rotation cooldown (seconds) | `300` |
| `EXCELMANUS_POOL_AUTO_HYSTERESIS_DELTA` | Auto-rotation hysteresis delta | `0.12` |
| `EXCELMANUS_POOL_AUTO_MIN_DWELL` | Minimum dwell on one account (seconds) | `180` |
| `EXCELMANUS_POOL_AUTO_BREAKER_OPEN` | Circuit-breaker open duration (seconds) | `120` |
| `EXCELMANUS_POOL_AUTO_BREAKER_THRESHOLD` | Circuit-breaker failure threshold | `5` |

## Tool Parameter Schema Validation

Performs JSON Schema-level validation on tool call parameters returned by the LLM, with three modes. Default is **shadow**: the call is not blocked; violations are written into the tool result for the model to correct.

| Setting key | Description | Default |
|---|---|---|
| `EXCELMANUS_TOOL_SCHEMA_VALIDATION_MODE` | `off`: no validation. `shadow`: record, do not block; violations appear in the tool result as `schema_validation` / `schema_violations` / `remediation`. `enforce`: block and return an error (`error_code=TOOL_ARGUMENT_VALIDATION_ERROR`, with `failure_class` / `remediation`) | `shadow` |
| `EXCELMANUS_TOOL_SCHEMA_VALIDATION_CANARY_PERCENT` | `enforce` mode canary percentage (0~100), 100 = full rollout | `100` |
| `EXCELMANUS_TOOL_SCHEMA_STRICT_PATH` | Strict path policy: path parameters must be relative and forbid `..` | `false` |

## Session snapshot

SessionState and the task list are saved at task claim, model-step boundaries, and
turn completion. Snapshots include the current main task, input options, step,
unconsumed guidance, and queued followups. SessionManager persists conversation
messages and tool results before the execution state that refers to them. This is
not a file checkpoint; file history lives in `.excelmanus/revisions/`.

After restart, an unfinished main task is `interrupted`. Reading its state does
not start execution. Send `/resume`, optionally followed by more instructions, to
continue from the saved conversation and tool results in a new turn. The prior chat
mode is retained and `resumed_from` identifies the earlier turn. Completed tools are
not automatically replayed; missing results remain explicitly unknown. When only
unclaimed messages remain, `/resume` wakes that queue.

`GET /api/v1/sessions/{session_id}/turn` reports the latest main turn, task, step,
queue count, and `can_resume`. Resume does not start a second copy of a running task.
This covers ordinary model/tool conversation boundaries. Approval and multi-question
state are also persisted: `/approve` and `/answer` record the decision or answer,
and `/resume` reconnects the original tool call through the normal chat stream.
States that still cannot be continued are reported in `resume_blocked_by`. The
original Python stack, external request, and Code Mode process locals are not restored;
an uncertain write is never automatically replayed.

Clearing a conversation also clears its recovery state and message cache. Snapshot
selection and retention use save order, including after turn numbers reset. Each
followup returns when its own turn finishes, independently of later queued turns.

## Code execution boundaries

`run_code` runs in a local subprocess and always keeps its timeout and workspace commit pipeline. **Ask** also restricts network access, child processes, and sensitive paths. **Skip** permits network access and child processes and must not be treated as security isolation. Workspace workbook changes should still use the authorized `em.*` SDK so commits and content versions follow the same path as direct tools. A failed script does not undo earlier committed calls.

## Thinking (Reasoning Depth)

| Setting key | Description | Default |
|---|---|---|
| `EXCELMANUS_THINKING_EFFORT` | Reasoning depth level (`none`/`minimal`/`low`/`medium`/`high`/`xhigh`/`max`) | `medium` |
| `EXCELMANUS_THINKING_BUDGET` | Exact token budget (overrides effort calculation when > 0) | `0` |
| `EXCELMANUS_THINKING_EFFORT_OPTIONS` | Comma-separated reasoning options; unsupported values are removed and an empty result falls back to the full list | `none,minimal,low,medium,high,xhigh,max` |

## OpenAI Responses API

| Setting key | Description | Default |
|---|---|---|
| `EXCELMANUS_USE_RESPONSES_API` | Set to `1` to enable Responses API (`/responses` endpoint), only effective for non-Gemini/Claude OpenAI-compatible URLs | `0` |

## System One / Jev

Jev is not a chat model and does not belong in `model_profiles`. TypeSafe, Vercel, and custom decision providers live under Settings → Model → Providers; Jev model choice and gates live under Settings → Model → Model roles. Both write `config_kv`.

| Setting key | Description | Default |
|---|---|---|
| `EXCELMANUS_JEV_ENABLED` | Master gate: `off` disables all packs, `enforce` fully enables them | `enforce` |
| `EXCELMANUS_JEV_EXPERIMENTAL_ENABLED` | Frontend experimental Jev entry gate; when off, provider settings, timeline button, inline records, and sidebar are hidden without deleting saved configuration | `false` |
| `EXCELMANUS_JEV_EXPOSURE` | Tool exposure and workspace/spreadsheet context suggestions: `off` / `enforce` | `enforce` |
| `EXCELMANUS_JEV_OBSERVATION` | Observation policy: `off` / `enforce` | `enforce` |
| `EXCELMANUS_JEV_VERIFICATION` | Post-mutation check suggestions: `off` / `enforce` | `enforce` |
| `EXCELMANUS_JEV_RECOVERY` | Error-recovery suggestions: `off` / `enforce` | `enforce` |
| `EXCELMANUS_JEV_MODE_HINT` | Mode-suggestion card; false disables this pack | `true` |
| `EXCELMANUS_JEV_UI_HINT` | End-of-turn UI hint; false disables this pack | `true` |
| `EXCELMANUS_JEV_MODEL` | Active decision model | `jev-1.13.0` |
| `EXCELMANUS_JEV_ACTIVE_PROVIDER` | Active decision provider id (`typesafe` / `vercel` / `custom-*`) | — |
| `EXCELMANUS_JEV_PROVIDERS` | Decision provider list (keys included, Fernet-encrypted) | `[]` |
| `EXCELMANUS_JEV_TIMEOUT_SECONDS` | Per-evaluation timeout | `1.5` |
| `EXCELMANUS_JEV_CALIBRATED` | Calibration switch for high-risk approval auto-allow; the approval pack must also have signed calibration provenance | `false` |
| `EXCELMANUS_TYPESAFE_API_KEY` | TypeSafe direct key (synced with the provider list) | — |
| `EXCELMANUS_AI_GATEWAY_API_KEY` | Vercel Gateway key (synced with the provider list) | — |
| `EXCELMANUS_MODEL_CANONICAL_MATCH` | Model-name matching under Model → Model roles: saving a profile binds the Model ID to a known canonical name when confidence is high enough, inheriting its context window and capability settings; the upstream Model ID is never rewritten, and enabling it back-fills existing profiles | `true` |

This optional feature requires the `system-one` extra. `off` disables the master gate or the selected pack; `enforce` applies enabled packs directly, with no observation-only runtime. Enabling the master gate fills omitted pack switches as enabled, while an explicitly disabled child switch still stops that pack. The advisory-only `context.resolve` pack supplies workspace, spreadsheet/range and clarification suggestions to the main model when both the master and exposure gates are `enforce`. It waits at most one additional second and never creates/switches workspaces or edits files. High-risk approval has a separate safety layer: Jev can change a high-risk call from human approval to automatic execution only when `EXCELMANUS_JEV_CALIBRATED=true` and the approval pack has signed calibration provenance; deny and ordinary advisory decisions are unaffected. Legacy `shadow` values are migrated to `enforce` when read.

## Encryption Configuration

Encrypted storage for sensitive fields (model API Keys, OAuth Access Tokens, etc.). Requires the `cryptography` dependency.

| Setting key | Description | Default |
|---|---|---|
| `EXCELMANUS_SECRET_KEY` | Fernet encryption key seed (locator; see above) | Auto-generated |

Key selection order:

1. Derive a key from `EXCELMANUS_SECRET_KEY`, if supplied at process startup, using SHA-256.
2. Read an existing `{EXCELMANUS_HOME}/.secret_key`.
3. Read a legacy `DATA_ROOT/.secret_key` or `~/.excelmanus/data/.secret_key` and attempt to migrate it to the canonical location.
4. Generate a new key at the canonical location if none exists, and restrict file permissions.

Encrypted credential writes fail when encryption is unavailable. If an existing database cannot be decrypted, restore its matching key; deleting or regenerating the key does not recover the stored credentials.

`FileAccessGuard` rejects read/write of `.secret_key`, `excelmanus.db`, `installations.json`, and leftover `.env` / `config.env` files, even when those files sit inside a registered workspace.

## Single-user workspace

One process has one data home (SQLite chat DB, memory, MCP config, model credentials). That is **not** multi-tenancy. Users can register multiple local folders as workspaces: each conversation binds one folder, and conversations in the same folder share that folder's files. Agent cwd, the file-access guard, revisions, and registry scans follow the current session's folder. Memory and MCP stay process-wide; they are not isolated per folder.

The default workspace is `EXCELMANUS_DATA_ROOT` (if set) or `EXCELMANUS_WORKSPACE_ROOT`, falling back to `EXCELMANUS_HOME/data` (`~/.excelmanus/data`). It is separate from the application checkout. Legacy automatically registered application roots are excluded from new-session choices; existing sessions and uploaded/output files remain in place.

File discovery, the sidebar, mentions and tool access ignore product source and build directories by default. Explicitly adopting a code directory through Add Workspace persists source access for that workspace, including across restarts. Automatic default registration grants no such access. Workspace boundaries, sensitive files and internal state remain protected. Adding a workspace adopts an existing local directory; it does not mkdir that path or store chat logs beside user files.

`EXCELMANUS_AUTH_ENABLED` / `NEXT_PUBLIC_AUTH_ENABLED` / `EXCELMANUS_SESSION_ISOLATION` are removed. Codex subscription OAuth remains (process-level, not bound to a login user).

The API listens on `127.0.0.1` by default. **Settings → Security → Login protection** enables or disables a single administrator gate for the whole instance. Set the username/password there; leave the password blank to retain it. Saving applies immediately, revokes all browser sessions and persists across restarts. Explicitly disabling protection permits direct access, including endpoints previously protected by the management token. There is no registration, user database or tenant isolation; model subscription OAuth remains independent.

The first server-mode startup (including loopback behind a proxy) or non-loopback bind requires an administrator password or management token, or previously saved login settings. Missing credentials and short passwords/tokens refuse startup. An explicit disabled choice saved in Settings is respected. Use HTTPS with a same-origin frontend/API reverse proxy. See [server login setup](server-login.md) for configuration and recovery.

Access settings and sessions live in `{EXCELMANUS_HOME}/access.db`, outside product configuration imports/exports. Saved passwords use salted scrypt hashes. Browsers receive only an HttpOnly, SameSite=Strict session cookie. Only login, status, minimal health and preflight requests are public; files, SSE, subscription APIs and API documentation require authentication. Anonymous health responses omit model, onboarding and session details. Sessions, revocation and the limit of 10 login attempts per minute are shared by workers on the same home directory.

### Manual `users/` migration

Do not auto-merge multiple `users/{id}` trees. If old isolation directories remain:

1. Pick the **one** `users/{id}/` you want to keep.
2. Copy its workspace files into the current `data_root` / `workspace_root`.
3. Per-user `data.db` files are **not** imported into the main database.
4. FileRegistry still skips directories named `users` so leftover archives are not scanned.

## Changelog

- 2026-09-23: Jev entry evaluations now share one turn budget and run concurrently; high-risk auto-allow requires signed calibration; context advice carries an explicit advisory trust envelope; the timeline exposes bounded probability distributions and turn metrics; UI hints time out without delaying the main reply.

- 2026-09-21: Added the `EXCELMANUS_WEB_UPGRADE_ENABLED` server web-upgrade switch, agent self-management (`EXCELMANUS_AGENT_SELF_MANAGEMENT_ENABLED`), and canonical model-name matching (`EXCELMANUS_MODEL_CANONICAL_MATCH`).

- 2026-09-19: Updated desktop locators, the 4096-token compaction budget, tool disclosure, capability probes, Jev verification/recovery settings, OAuth, and encryption-key migration.

- 2026-09-19: Jev settings moved from Runtime to Model → Providers / Model roles. TypeSafe and Vercel are separate presets; custom decision providers are supported.
- 2026-09-18: Product settings live only in `config_kv` / `model_profiles` plus the in-process overlay. Jev gates and keys use that same chain (Web Settings runtime fields). Locators (`HOME` / `DB_PATH` / `DATA_ROOT` / `DEPLOY_MODE` / ports / `MANAGE_TOKEN`) still come from the start process. `deploy/.env.deploy` and `web/.env.local` are the ops-host inventory and the Next.js runtime origin. MCP `mcp.json` `$VAR` expands only for MCP child processes.
