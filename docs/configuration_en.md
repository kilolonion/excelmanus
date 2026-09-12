# Configuration Reference

Priority: non-empty process environment > project-root `.env` > `$EXCELMANUS_HOME/config.env` > defaults.

Empty `KEY=` values (from `.env.example` copies or Docker `env_file`) **do not** hide keys already saved in the canonical store.

Settings UI / config import write env items to **`$EXCELMANUS_HOME/config.env`** (default `~/.excelmanus/config.env`). The project-root `.env` is a convenience file; if it already exists, saves are mirrored there.

Model-profile API keys are encrypted in the main database. The Fernet key lives at `$EXCELMANUS_HOME/data/.secret_key`. Keep both on the same persistent volume, or keys cannot be decrypted after restart.

## Basic Configuration

| Environment Variable | Description | Default |
|---|---|---|
| `EXCELMANUS_API_KEY` | LLM API Key (required) | — |
| `EXCELMANUS_BASE_URL` | LLM API endpoint (required) | — |
| `EXCELMANUS_MODEL` | Model name (required; Gemini can be auto-extracted from BASE_URL) | — |
| `EXCELMANUS_PROTOCOL` | Model protocol type (`auto`/`openai`/`openai_responses`/`anthropic`/`gemini`) | `auto` |
| `EXCELMANUS_MAX_ITERATIONS` | Per-turn cap on LLM rounds and tool calls (each parallel tool counts as 1) | `50` |
| `EXCELMANUS_MAX_CONSECUTIVE_FAILURES` | Consecutive failure circuit-breaker threshold | `6` |
| `EXCELMANUS_SESSION_TTL_SECONDS` | API session idle timeout (seconds) | `1800` |
| `EXCELMANUS_MAX_SESSIONS` | Maximum concurrent API sessions | `1000` |
| `EXCELMANUS_HOME` | Durable home (`config.env`, default DB, encryption keys) | `~/.excelmanus` |
| `EXCELMANUS_WORKSPACE_ROOT` | File access whitelist root directory | `.` |
| `EXCELMANUS_DATA_ROOT` | Centralized data directory (uploads / secret files) | `{EXCELMANUS_HOME}/data` |
| `EXCELMANUS_DEPLOY_MODE` | Deployment mode (`auto`/`standalone`/`server`). `auto` and unknown values are standalone; `server` must be set explicitly | `auto` |
| `EXCELMANUS_LOG_LEVEL` | Log level | `INFO` |
| `EXCELMANUS_CORS_ALLOW_ORIGINS` | API CORS allowed origins (comma-separated) | `http://localhost:3000` |
| `EXCELMANUS_MAX_CONTEXT_TOKENS` | Conversation context token limit | `128000` |
| `EXCELMANUS_PROMPT_CACHE_KEY_ENABLED` | Send prompt_cache_key to API to improve cache hit rate | `true` |
| `EXCELMANUS_CLI_LAYOUT_MODE` | CLI layout mode (`dashboard`/`classic`) | `dashboard` |

## Skillpack & Routing Configuration

| Environment Variable | Description | Default |
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
| `EXCELMANUS_CLAWHUB_ENABLED` | Enable ClawHub skill marketplace | `true` |
| `EXCELMANUS_CLAWHUB_REGISTRY_URL` | ClawHub registry URL | `https://clawhub.ai` |
| `EXCELMANUS_CLAWHUB_PREFER_CLI` | ClawHub prefers CLI installation | `true` |
| `EXCELMANUS_TOOL_RESULT_HARD_CAP_CHARS` | Tool result global hard truncation length (0 = unlimited) | `12000` |

## Subagent Configuration

| Environment Variable | Description | Default |
|---|---|---|
| `EXCELMANUS_LARGE_EXCEL_THRESHOLD_BYTES` | Threshold for triggering large-file subagent delegation prompt (bytes) | `8388608` |
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

| Environment Variable | Description | Default |
|---|---|---|
| `EXCELMANUS_COMPACTION_ENABLED` | Enable auto-compaction | `true` |
| `EXCELMANUS_COMPACTION_THRESHOLD_RATIO` | Context ratio threshold to trigger compaction | `0.85` |
| `EXCELMANUS_COMPACTION_KEEP_RECENT_TURNS` | Number of recent turns to keep during compaction | `5` |
| `EXCELMANUS_COMPACTION_MAX_SUMMARY_TOKENS` | Maximum tokens for compaction summary | `1500` |
| `EXCELMANUS_SUMMARIZATION_ENABLED` | Legacy summarization layer (off by default; compaction covers this) | `false` |
| `EXCELMANUS_SUMMARIZATION_THRESHOLD_RATIO` | Summarization trigger threshold | `0.8` |
| `EXCELMANUS_SUMMARIZATION_KEEP_RECENT_TURNS` | Number of recent turns to keep during summarization | `3` |

## Hook Configuration

| Environment Variable | Description | Default |
|---|---|---|
| `EXCELMANUS_HOOKS_COMMAND_ENABLED` | Allow `command` hook execution | `false` |
| `EXCELMANUS_HOOKS_COMMAND_ALLOWLIST` | `command` hook allowlist prefixes (comma-separated) | empty |
| `EXCELMANUS_HOOKS_COMMAND_TIMEOUT_SECONDS` | `command` hook timeout (seconds) | `10` |
| `EXCELMANUS_HOOKS_OUTPUT_MAX_CHARS` | Hook output truncation length | `32000` |

## Routing Behavior

- Tool schemas are built each request (meta tools + domain tools). `plan` and `write` see the same catalog; seeing a write tool does not mean the workbook may be changed.
- Skills come from a user-role catalog snapshot. The model loads a body with `skill`; a user `/name` gesture also injects `<skill-invocation>`. Neither changes tool visibility.

## System Message Mode

`EXCELMANUS_SYSTEM_MESSAGE_MODE` (default `auto`):

- `replace`: Multiple system segments injected separately.
- `merge`: Merged into a single system message.
- `auto`: Defaults to `replace`; automatically falls back to `merge` when encountering provider multi-system compatibility errors.

## Multi-Model

> **Note**: The `EXCELMANUS_MODELS` environment variable is deprecated. Model profiles have been migrated to database management via the Web settings page or `/model` command. On first launch, if this env var exists it will be auto-migrated to the database.

- Only one model is active. `/model <name>` switches the active profile.
- Chat, subagents, compaction, and memory extraction all use that active model.

## Vision

Images go to the active model only. If it has no vision, attachments are rejected. If it does, `read_image` or workbench attachments inject the picture; the model writes a `WorkbookSpec` and calls `edit_spreadsheet(workbook_spec=)`. There is no separate vision pipeline and no auxiliary VLM description.

| Environment Variable | Description | Default |
|---|---|---|
| `EXCELMANUS_MAIN_MODEL_VISION` | Active model vision capability (`auto`/`true`/`false`) | `auto` |
| `EXCELMANUS_IMAGE_KEEP_ROUNDS` | Minimum rounds to keep full image base64 in context | `3` |

## Backup sandbox

Backup overlay is removed. Writes land on the user path; history lives in `.excelmanus/revisions/`.

## Code Policy Engine Configuration

Performs static analysis on code executed by `run_code`, automatically routing approval by security level.

| Environment Variable | Description | Default |
|---|---|---|
| `EXCELMANUS_CODE_POLICY_ENABLED` | Enable code policy engine | `true` |
| `EXCELMANUS_CODE_POLICY_GREEN_AUTO` | Auto-approve Green-level (safe) code | `true` |
| `EXCELMANUS_CODE_POLICY_YELLOW_AUTO` | Auto-approve Yellow-level code (off by default; filesystem writes are never auto-approved) | `false` |
| `EXCELMANUS_CODE_POLICY_EXTRA_SAFE` | Extra safe module allowlist (comma-separated) | empty |
| `EXCELMANUS_CODE_POLICY_EXTRA_BLOCKED` | Extra blocked module blocklist (comma-separated) | empty |

## Embedding Semantic Search Configuration

Provides semantic search for persistent memory, file manifests, and error solutions. Requires an embedding API **and** explicit `EXCELMANUS_EMBEDDING_ENABLED=true`; the client is not constructed on the default path.

When enabled:
- **SemanticRegistry** — Semantic file summary injection
- **ErrorSolutionStore** — Error→solution vector index, persisted to `.excelmanus/error_solutions/`
- **Memory Semantic Dedup** — New memories compared against existing entries via cosine similarity, filtering duplicates
- **Smart Context Compaction** — Relevance-scored differential truncation during compaction (high-relevance messages retain more)

All integration points share a single `_embedding_client`. When `embedding_enabled=false`, these features degrade to no-ops.

| Environment Variable | Description | Default |
|---|---|---|
| `EXCELMANUS_EMBEDDING_ENABLED` | Enable semantic search (must be set explicitly) | `false` |
| `EXCELMANUS_EMBEDDING_API_KEY` | Embedding API Key | — |
| `EXCELMANUS_EMBEDDING_BASE_URL` | Embedding API Base URL | — |
| `EXCELMANUS_EMBEDDING_MODEL` | Embedding model name | `text-embedding-3-small` |
| `EXCELMANUS_EMBEDDING_DIMENSIONS` | Vector dimensions | `1536` |
| `EXCELMANUS_EMBEDDING_TIMEOUT_SECONDS` | Request timeout (seconds) | `30.0` |
| `EXCELMANUS_MEMORY_SEMANTIC_TOP_K` | Memory semantic search Top-K | `10` |
| `EXCELMANUS_MEMORY_SEMANTIC_THRESHOLD` | Memory semantic search threshold | `0.3` |
| `EXCELMANUS_MEMORY_SEMANTIC_FALLBACK_RECENT` | Fallback recent entries on semantic search failure | `5` |
| `EXCELMANUS_REGISTRY_SEMANTIC_TOP_K` | File registry semantic search Top-K | `5` |
| `EXCELMANUS_REGISTRY_SEMANTIC_THRESHOLD` | File registry semantic search threshold | `0.25` |

## Persistent Memory

| Environment Variable | Description | Default |
|---|---|---|
| `EXCELMANUS_MEMORY_ENABLED` | Global memory switch | `true` |
| `EXCELMANUS_MEMORY_DIR` | Memory directory | `~/.excelmanus/memory` |
| `EXCELMANUS_MEMORY_AUTO_LOAD_LINES` | Auto-load line count | `200` |
| `EXCELMANUS_MEMORY_AUTO_EXTRACT_INTERVAL` | Background silent memory extraction every N turns (0 = disabled) | `0` |

Topic files: `file_patterns.md`, `user_prefs.md`, `error_solutions.md`, `general.md`.
The core file `MEMORY.md` is synced to topic files on save, used for automatic loading at session startup.

When Embedding is enabled, memory retrieval automatically switches to semantic matching mode, and newly extracted memories are deduplicated against existing entries via cosine similarity (threshold 0.88), automatically filtering redundancies.

## Playbook (tactical handbook)

Optional SQLite store, off by default. When enabled, `/playbook` lists entries. The default path does not auto-curate or inject bullets into each turn.

| Environment Variable | Description | Default |
|---|---|---|
| `EXCELMANUS_PLAYBOOK_ENABLED` | Enable Playbook storage | `false` |
| `EXCELMANUS_PLAYBOOK_DB_PATH` | Playbook database path (empty uses default path) | empty |
| `EXCELMANUS_PLAYBOOK_MAX_BULLETS` | Maximum Playbook entries | `500` |

## Built-in Search Engines

Three search engine MCP Servers are built-in (Exa / Tavily / Brave), requiring no manual `mcp.json` configuration.

**Enabling strategy** ("default + optional" mode):
1. `EXCELMANUS_EXA_SEARCH=false` → disables all built-in search engines
2. The engine specified by `EXCELMANUS_SEARCH_DEFAULT` is always enabled
3. Non-default engines are additionally enabled only when their API key is configured
4. If the default engine is tavily/brave but lacks an API key or Node.js → auto-fallback to exa

Tavily and Brave are launched via `npx` (stdio transport) and require Node.js. Exa connects via HTTP and does not require Node.js.

| Environment Variable | Description | Default |
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

| Environment Variable | Description | Default |
|---|---|---|
| `EXCELMANUS_MCP_SHARED_MANAGER` | Whether API sessions reuse a shared MCP manager | `false` |
| `EXCELMANUS_MCP_ENABLE_STREAMABLE_HTTP` | Enable streamable_http transport | `false` |
| `EXCELMANUS_MCP_UNDEFINED_ENV` | Undefined environment variable policy (`keep`/`empty`/`error`) | `keep` |
| `EXCELMANUS_MCP_STRICT_SECRETS` | Block loading on plaintext sensitive fields | `false` |

`mcp.json` capabilities:
- `transport` supports `stdio`, `sse`, `streamable_http`.
- Supports `$VAR` / `${VAR}` environment variable references in `args/env/url/headers`.
- MCP only registers `mcp_*` tools; Skillpacks handle policy and authorization. If a Skillpack requires MCP, declare `required-mcp-servers` / `required-mcp-tools` in `SKILL.md`.

MCP security scanning:
- Local: `scripts/security/scan_secrets.sh`
- pre-commit: Built-in hook in `.pre-commit-config.yaml`
- CI: `.github/workflows/security-secrets.yml`

## Unified Database

| Environment Variable | Description | Default |
|---|---|---|
| `EXCELMANUS_DB_PATH` | SQLite database path (chat history, memory, vectors, approvals all stored here) | `~/.excelmanus/excelmanus.db` |

## Chat History Persistence

| Environment Variable | Description | Default |
|---|---|---|
| `EXCELMANUS_CHAT_HISTORY_ENABLED` | Enable chat history persistence | `true` |

## Tool Parameter Schema Validation

Performs JSON Schema-level validation on tool call parameters returned by the LLM, with three modes.

| Environment Variable | Description | Default |
|---|---|---|
| `EXCELMANUS_TOOL_SCHEMA_VALIDATION_MODE` | `off` (disabled) / `shadow` (log only, no blocking) / `enforce` (block and return error) | `off` |
| `EXCELMANUS_TOOL_SCHEMA_VALIDATION_CANARY_PERCENT` | `enforce` mode canary percentage (0~100), 100 = full rollout | `100` |
| `EXCELMANUS_TOOL_SCHEMA_STRICT_PATH` | Strict path policy: path parameters must be relative and forbid `..` | `false` |

## Session snapshot

After each turn, SessionState / task list is saved to the `session_checkpoints` table for session restore. This is not a file checkpoint; file history lives in `.excelmanus/revisions/`.

## Code sandbox

`run_code` always uses the local subprocess fence: no network, no subprocess spawn, no writes outside the workspace. Compose / image install is also no longer a product path.

## Thinking (Reasoning Depth)

| Environment Variable | Description | Default |
|---|---|---|
| `EXCELMANUS_THINKING_EFFORT` | Reasoning depth level (`none`/`minimal`/`low`/`medium`/`high`/`xhigh`/`max`) | `medium` |
| `EXCELMANUS_THINKING_BUDGET` | Exact token budget (overrides effort calculation when > 0) | `0` |

## OpenAI Responses API

| Environment Variable | Description | Default |
|---|---|---|
| `EXCELMANUS_USE_RESPONSES_API` | Set to `1` to enable Responses API (`/responses` endpoint), only effective for non-Gemini/Claude OpenAI-compatible URLs | `0` |

## Encryption Configuration

Encrypted storage for sensitive fields (model API Keys, OAuth Access Tokens, etc.). Requires the `cryptography` dependency.

| Environment Variable | Description | Default |
|---|---|---|
| `EXCELMANUS_SECRET_KEY` | Fernet encryption key seed (auto-generates at `{EXCELMANUS_HOME}/data/.secret_key` if empty) | Auto-generated |

Key derivation priority:
1. `EXCELMANUS_SECRET_KEY` environment variable (SHA-256 derived)
2. `{EXCELMANUS_HOME}/data/.secret_key` auto-generated (created on first launch, file permissions 600)
3. Legacy `~/.excelmanus/data/.secret_key` (copied to the canonical path if present)
4. When neither is available, encryption is disabled (development only)

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
