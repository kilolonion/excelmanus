# Configuration Reference

User settings live only in the **main database** (`config_kv` and `model_profiles` in `~/.excelmanus/excelmanus.db`). The settings UI, config import, and `/config` all write there. `load_config()` reads this store only.

After launch, add a model in Web Settings; the server starts in degraded mode until a profile is saved.

The process may use a few **locators** to find the data volume and bind ports. Start scripts / systemd set these. They are **not** a settings overlay:

| Locator | Description | Default |
|---|---|---|
| `EXCELMANUS_HOME` | Durable home (main database, encryption keys) | `~/.excelmanus` |
| `EXCELMANUS_DB_PATH` | Main database path | `{EXCELMANUS_HOME}/excelmanus.db` |
| `EXCELMANUS_DATA_ROOT` | Centralized data directory (uploads / outputs; secrets are not stored here) | `{EXCELMANUS_HOME}/data` |
| `EXCELMANUS_DEPLOY_MODE` | `auto`/`standalone`/`server`. `auto` and unknown values are standalone; `server` must be set explicitly | `auto` |
| `EXCELMANUS_API_HOST` / `EXCELMANUS_API_PORT` / `EXCELMANUS_BACKEND_PORT` / `EXCELMANUS_FRONTEND_PORT` | Bind address and ports | See start scripts |
| `EXCELMANUS_WEB_WORKERS` | uvicorn worker count; API logs a cache-bust WARNING when `>1`. Keep `1` on a single host | Set by `deploy/start.*`, default `1` |
| `EXCELMANUS_MANAGE_TOKEN` | Required when binding a non-loopback address (at least 16 characters). When set, every `/api/v1` route except health requires `Authorization: Bearer` | empty (loopback may omit it) |
| `EXCELMANUS_SECRET_KEY` | Fernet key seed (tests / custom volumes) | empty → `{EXCELMANUS_HOME}/.secret_key` |

Do not put model secrets or runtime options in the process environment; leftover product keys are ignored and logged.

Names in the tables below are `config_kv` keys and match the settings UI fields.

Model-profile API keys are encrypted in the main database. The Fernet key lives at `$EXCELMANUS_HOME/.secret_key` (it does not follow DATA_ROOT, so it cannot fall into an Agent workspace). Keep both on the same persistent volume, or keys cannot be decrypted after restart.

## Basic Configuration

| Setting key | Description | Default |
|---|---|---|
| `EXCELMANUS_API_KEY` | `config_kv` fallback when no active profile; add a profile in Settings | — |
| `EXCELMANUS_BASE_URL` | `config_kv` fallback when no active profile | — |
| `EXCELMANUS_MODEL` | `config_kv` fallback when no active profile; Gemini can be auto-extracted from BASE_URL | — |
| `EXCELMANUS_PROTOCOL` | Model protocol type (`auto`/`openai`/`openai_responses`/`anthropic`/`gemini`) | `auto` |
| `EXCELMANUS_MAX_ITERATIONS` | Per-turn cap on LLM rounds and tool calls (each parallel tool counts as 1) | `50` |
| `EXCELMANUS_MAX_CONSECUTIVE_FAILURES` | Consecutive failure circuit-breaker threshold | `6` |
| `EXCELMANUS_SESSION_TTL_SECONDS` | API session idle timeout (seconds) | `1800` |
| `EXCELMANUS_MAX_SESSIONS` | Maximum concurrent API sessions | `1000` |
| `EXCELMANUS_WORKSPACE_ROOT` | File access whitelist root directory | `.` |
| `EXCELMANUS_LOG_LEVEL` | Log level | `INFO` |
| `EXCELMANUS_CORS_ALLOW_ORIGINS` | API CORS allowed origins (comma-separated). Runtime also adds `localhost` / `127.0.0.1` / `[::1]` plus the frontend port | `http://localhost:3000,http://127.0.0.1:3000` |
| `EXCELMANUS_MAX_CONTEXT_TOKENS` | Conversation context token limit | `128000` |
| `EXCELMANUS_PROMPT_CACHE_KEY_ENABLED` | Send prompt_cache_key to API to improve cache hit rate | `true` |

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
| `EXCELMANUS_SUBAGENT_MAX_ITERATIONS` | Child-loop cap on LLM rounds and tool calls | `120` |
| `EXCELMANUS_SUBAGENT_MAX_CONSECUTIVE_FAILURES` | Subagent consecutive failure circuit-breaker threshold | `6` |
| `EXCELMANUS_SUBAGENT_TIMEOUT_SECONDS` | Single subagent execution timeout (seconds) | `600` |
| `EXCELMANUS_PARALLEL_SUBAGENT_MAX` | Maximum parallel subagent concurrency | `3` |
| `EXCELMANUS_PARALLEL_READONLY_TOOLS` | Concurrent execution of adjacent read-only tools in the same turn | `true` |
| `EXCELMANUS_SUBAGENT_USER_DIR` | User-level subagent directory | `~/.excelmanus/agents` |
| `EXCELMANUS_SUBAGENT_PROJECT_DIR` | Project-level subagent directory | `<workspace_root>/.excelmanus/agents` |

## Context Auto-Compaction

When the conversation exceeds the threshold, the active model compresses earlier dialogue in the background without blocking the main pipeline.

| Setting key | Description | Default |
|---|---|---|
| `EXCELMANUS_COMPACTION_ENABLED` | Enable auto-compaction | `true` |
| `EXCELMANUS_COMPACTION_THRESHOLD_RATIO` | Context ratio threshold to trigger compaction | `0.85` |
| `EXCELMANUS_COMPACTION_KEEP_RECENT_TURNS` | Number of recent turns to keep during compaction | `5` |
| `EXCELMANUS_COMPACTION_MAX_SUMMARY_TOKENS` | Maximum tokens for compaction summary | `1500` |

## Hook Configuration

| Setting key | Description | Default |
|---|---|---|
| `EXCELMANUS_HOOKS_COMMAND_ENABLED` | Allow `command` hook execution | `false` |
| `EXCELMANUS_HOOKS_COMMAND_ALLOWLIST` | `command` hook allowlist prefixes (comma-separated) | empty |
| `EXCELMANUS_HOOKS_COMMAND_TIMEOUT_SECONDS` | `command` hook timeout (seconds) | `10` |
| `EXCELMANUS_HOOKS_OUTPUT_MAX_CHARS` | Hook output truncation length | `32000` |

## Routing Behavior

- Each request derives the visible catalog via `EffectiveToolCatalog` (`names()` / `tool_schemas()` / `tool_index_text()` / `introspection_source()` / `digest()` share one derivation). Catalog visibility uses `policy.is_catalog_visible(tool_name, declared)`: no write effect, or the tool has a read-only action (`policy.has_readonly_action`). The `READ_ONLY_SAFE_TOOLS` whitelist no longer drives catalog visibility (it still feeds approval / parallel-read decisions).
- `read` / `plan` catalogs exclude pure write tools. `manage_spreadsheet_versions` stays visible in read/plan because of its read-only `list` action; `checkpoint` / `restore` are still intercepted at execute time by `write_effect_for_call` as `workspace_write`. `write` sees the full catalog; `code` sees only `run_code`. Seeing a tool does not mean the workbook may be changed.
- Skills come from a user-role catalog snapshot. The model loads a body with `skill`; a user `/name` gesture also injects `<skill-invocation>`. Neither changes tool visibility.

## Multi-Model

> **Note**: `EXCELMANUS_MODELS` is removed. Model profiles live only in the main database via the Web settings page.

- Only one model is active. `/model <name>` switches the active profile.
- Chat, subagents, compaction, and memory extraction all use that active model.

## Vision

Images go to the active model only. If it has no vision, attachments are rejected. If it does, `read_image` or workbench attachments inject the picture; the model writes a `WorkbookSpec` and calls `edit_spreadsheet(workbook_spec=)`. There is no separate vision pipeline and no auxiliary VLM description.

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

## Backup sandbox

Backup overlay is removed. Writes land on the user path; history lives in `.excelmanus/revisions/`.

The first workspace open after upgrade imports leftover `outputs/backups` into RevisionStore once; the marker is `.excelmanus/migrations/overlay-backups.json`. To re-run:

```bash
python -m excelmanus.workspace.migrate <workspace> --force
```

## Code Policy Engine Configuration

Performs static analysis on code executed by `run_code`, automatically routing approval by security level.

| Setting key | Description | Default |
|---|---|---|
| `EXCELMANUS_CODE_POLICY_ENABLED` | Enable code policy engine | `true` |
| `EXCELMANUS_CODE_POLICY_GREEN_AUTO` | Auto-approve Green-level (safe) code | `true` |
| `EXCELMANUS_CODE_POLICY_YELLOW_AUTO` | Auto-approve Yellow-level code (off by default; filesystem writes are never auto-approved) | `false` |
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
| `EXCELMANUS_MCP_SHARED_MANAGER` | Whether API sessions reuse a shared MCP manager | `false` |
| `EXCELMANUS_MCP_ENABLE_STREAMABLE_HTTP` | Enable streamable_http transport | `false` |
| `EXCELMANUS_MCP_UNDEFINED_ENV` | Undefined environment variable policy (`keep`/`empty`/`error`) | `keep` |
| `EXCELMANUS_MCP_STRICT_SECRETS` | Block loading on plaintext sensitive fields | `false` |

`mcp.json` capabilities:
- `transport` supports `stdio`, `sse`, `streamable_http`.
- Supports `$VAR` / `${VAR}` in `args/env/url/headers`. That expands **the MCP child process environment** (secrets for that server), not the product settings store.
- MCP only registers `mcp_*` tools; Skillpacks handle policy and authorization. If a Skillpack requires MCP, declare `required-mcp-servers` / `required-mcp-tools` in `SKILL.md`.

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

After each turn, SessionState / task list is saved to the `session_state_snapshots` table for session restore. This is not a file checkpoint; file history lives in `.excelmanus/revisions/`.

## Code sandbox

`run_code` always uses the local subprocess fence: no network, no subprocess spawn, no writes outside the workspace. Compose / image install is also no longer a product path.

## Thinking (Reasoning Depth)

| Setting key | Description | Default |
|---|---|---|
| `EXCELMANUS_THINKING_EFFORT` | Reasoning depth level (`none`/`minimal`/`low`/`medium`/`high`/`xhigh`/`max`) | `medium` |
| `EXCELMANUS_THINKING_BUDGET` | Exact token budget (overrides effort calculation when > 0) | `0` |

## OpenAI Responses API

| Setting key | Description | Default |
|---|---|---|
| `EXCELMANUS_USE_RESPONSES_API` | Set to `1` to enable Responses API (`/responses` endpoint), only effective for non-Gemini/Claude OpenAI-compatible URLs | `0` |

## System One / Jev

Jev is not a chat model and does not belong in `model_profiles`. TypeSafe, Vercel, and custom decision providers live under Settings → Model → Providers; Jev model choice and gates live under Settings → Model → Model roles. Both write `config_kv`.

| Setting key | Description | Default |
|---|---|---|
| `EXCELMANUS_JEV_ENABLED` | Master gate: `off` / `shadow` / `enforce` | `off` |
| `EXCELMANUS_JEV_EXPOSURE` | Exposure-family gate | `off` |
| `EXCELMANUS_JEV_OBSERVATION` | Observation-family gate | `off` |
| `EXCELMANUS_JEV_MODE_HINT` | Mode-suggestion card | `false` |
| `EXCELMANUS_JEV_PRESENT_AS_AUTO` | Transient present_as | `false` |
| `EXCELMANUS_JEV_UI_HINT` | End-of-turn UI hint | `false` |
| `EXCELMANUS_JEV_MODEL` | Active decision model | `jev-1.13.0` |
| `EXCELMANUS_JEV_ACTIVE_PROVIDER` | Active decision provider id (`typesafe` / `vercel` / `custom-*`) | — |
| `EXCELMANUS_JEV_PROVIDERS` | Decision provider list (keys included, Fernet-encrypted) | `[]` |
| `EXCELMANUS_JEV_TIMEOUT_SECONDS` | Per-evaluation timeout | `1.5` |
| `EXCELMANUS_JEV_CALIBRATED` | Enforce side effects only after Chinese live sign-off | `false` |
| `EXCELMANUS_TYPESAFE_API_KEY` | TypeSafe direct key (synced with the provider list) | — |
| `EXCELMANUS_AI_GATEWAY_API_KEY` | Vercel Gateway key (synced with the provider list) | — |

Until calibration is signed, `enforce` does not `applied`. The calibrator `bench/jev_live_calibrate.py` reads the same store.

## Encryption Configuration

Encrypted storage for sensitive fields (model API Keys, OAuth Access Tokens, etc.). Requires the `cryptography` dependency.

| Setting key | Description | Default |
|---|---|---|
| `EXCELMANUS_SECRET_KEY` | Fernet encryption key seed (locator; see above) | Auto-generated |

Key derivation priority:
1. `EXCELMANUS_SECRET_KEY` if already set on the process at start (SHA-256 derived; tests / custom volumes)
2. `{EXCELMANUS_HOME}/.secret_key` auto-generated (created on first launch, file permissions 600)
3. Legacy `DATA_ROOT/.secret_key` / `~/.excelmanus/data/.secret_key` (read and migrated to the canonical path)
4. When none are available, encryption is disabled (development only)

`FileAccessGuard` rejects read/write of `.secret_key`, `excelmanus.db`, `installations.json`, and leftover `.env` / `config.env` files, even when those files sit inside a registered workspace.

## Single-user workspace

One process has one data home (SQLite chat DB, memory, MCP config, model credentials). That is **not** multi-tenancy. Users can register multiple local folders as workspaces: each conversation binds one folder, and conversations in the same folder share that folder's files. Agent cwd, the file-access guard, revisions, and registry scans follow the current session's folder. Memory and MCP stay process-wide; they are not isolated per folder.

The default workspace is `EXCELMANUS_DATA_ROOT` (if set) or `EXCELMANUS_WORKSPACE_ROOT`. The chats tab can adopt an existing local directory; it does not mkdir that path, and it does not store chat logs next to the xlsx files.

`EXCELMANUS_AUTH_ENABLED` / `NEXT_PUBLIC_AUTH_ENABLED` / `EXCELMANUS_SESSION_ISOLATION` are removed. Codex subscription OAuth remains (process-level, not bound to a login user).

The API listens on `127.0.0.1` by default. Binding a non-loopback address requires `EXCELMANUS_MANAGE_TOKEN` (at least 16 characters). When that token is set, every `/api/v1` route except health requires `Authorization: Bearer`. Server deploys should reverse-proxy to `127.0.0.1:8000` instead of exposing the app port on `0.0.0.0`.

### Manual `users/` migration

Do not auto-merge multiple `users/{id}` trees. If old isolation directories remain:

1. Pick the **one** `users/{id}/` you want to keep.
2. Copy its workspace files into the current `data_root` / `workspace_root`.
3. Per-user `data.db` files are **not** imported into the main database.
4. FileRegistry still skips directories named `users` so leftover archives are not scanned.

## Changelog

- 2026-09-19: Jev settings moved from Runtime to Model → Providers / Model roles. TypeSafe and Vercel are separate presets; custom decision providers are supported.
- 2026-09-18: Product settings live only in `config_kv` / `model_profiles` plus the in-process overlay. Jev gates and keys use that same chain (Web Settings runtime fields). Locators (`HOME` / `DB_PATH` / `DATA_ROOT` / `DEPLOY_MODE` / ports / `MANAGE_TOKEN`) still come from the start process. `deploy/.env.deploy` and `web/.env.local` are the ops-host inventory and the Next.js runtime origin. MCP `mcp.json` `$VAR` expands only for MCP child processes.
