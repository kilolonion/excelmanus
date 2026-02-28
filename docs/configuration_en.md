# Configuration Reference

Priority: Environment variables > `.env` > Default values.

## Basic Configuration

| Environment Variable | Description | Default |
|---|---|---|
| `EXCELMANUS_API_KEY` | LLM API Key (required) | — |
| `EXCELMANUS_BASE_URL` | LLM API endpoint (required) | — |
| `EXCELMANUS_MODEL` | Model name (required; Gemini can be auto-extracted from BASE_URL) | — |
| `EXCELMANUS_MAX_ITERATIONS` | Maximum agent iteration rounds | `50` |
| `EXCELMANUS_MAX_CONSECUTIVE_FAILURES` | Consecutive failure circuit-breaker threshold | `6` |
| `EXCELMANUS_SESSION_TTL_SECONDS` | API session idle timeout (seconds) | `1800` |
| `EXCELMANUS_MAX_SESSIONS` | Maximum concurrent API sessions | `1000` |
| `EXCELMANUS_WORKSPACE_ROOT` | File access whitelist root directory | `.` |
| `EXCELMANUS_LOG_LEVEL` | Log level | `INFO` |
| `EXCELMANUS_EXTERNAL_SAFE_MODE` | External safe mode (hides thinking/tool details and routing metadata) | `true` |
| `EXCELMANUS_CORS_ALLOW_ORIGINS` | API CORS allowed origins (comma-separated) | `http://localhost:3000, http://localhost:5173` |
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
| `EXCELMANUS_AUX_ENABLED` | AUX master switch (`false` to fall back to main model even if AUX is configured) | `true` |
| `EXCELMANUS_AUX_API_KEY` | AUX API Key (routing + subagent default model + window advisor) | — |
| `EXCELMANUS_AUX_BASE_URL` | AUX Base URL (falls back to main config if not set) | — |
| `EXCELMANUS_AUX_MODEL` | AUX model name (falls back to main model if not set) | — |
| `EXCELMANUS_TOOL_RESULT_HARD_CAP_CHARS` | Tool result global hard truncation length (0 = unlimited) | `12000` |

## Subagent Configuration

| Environment Variable | Description | Default |
|---|---|---|
| `EXCELMANUS_LARGE_EXCEL_THRESHOLD_BYTES` | Threshold for triggering large-file subagent delegation prompt (bytes) | `8388608` |
| `EXCELMANUS_SUBAGENT_ENABLED` | Enable subagent execution | `true` |
| `EXCELMANUS_AUX_MODEL` | Auxiliary model (routing + subagent default model + window advisor model) | — |
| `EXCELMANUS_SUBAGENT_MAX_ITERATIONS` | Subagent maximum iteration rounds | `120` |
| `EXCELMANUS_SUBAGENT_MAX_CONSECUTIVE_FAILURES` | Subagent consecutive failure circuit-breaker threshold | `6` |
| `EXCELMANUS_SUBAGENT_TIMEOUT_SECONDS` | Single subagent execution timeout (seconds) | `600` |
| `EXCELMANUS_PARALLEL_SUBAGENT_MAX` | Maximum parallel subagent concurrency | `3` |
| `EXCELMANUS_PARALLEL_READONLY_TOOLS` | Concurrent execution of adjacent read-only tools in the same turn | `true` |
| `EXCELMANUS_SUBAGENT_USER_DIR` | User-level subagent directory | `~/.excelmanus/agents` |
| `EXCELMANUS_SUBAGENT_PROJECT_DIR` | Project-level subagent directory | `<workspace_root>/.excelmanus/agents` |

## Context Auto-Compaction

When the conversation exceeds the threshold, the auxiliary model compresses earlier dialogue in the background without blocking the main pipeline. Requires `EXCELMANUS_AUX_MODEL` to be configured.

| Environment Variable | Description | Default |
|---|---|---|
| `EXCELMANUS_COMPACTION_ENABLED` | Enable auto-compaction | `true` |
| `EXCELMANUS_COMPACTION_THRESHOLD_RATIO` | Context ratio threshold to trigger compaction | `0.85` |
| `EXCELMANUS_COMPACTION_KEEP_RECENT_TURNS` | Number of recent turns to keep during compaction | `5` |
| `EXCELMANUS_COMPACTION_MAX_SUMMARY_TOKENS` | Maximum tokens for compaction summary | `1500` |
| `EXCELMANUS_SUMMARIZATION_ENABLED` | Enable conversation history summarization | `true` |
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

- Tool schemas are dynamically built before each round based on `write_hint` (default injects meta-tools + domain tools).
- When `write_hint=read_only`, only the read-only tool subset is exposed (while retaining `run_code` and persistent meta-tools) to reduce schema token overhead.
- `activate_skill` only injects domain knowledge guidance (pure knowledge injection; does not control tool visibility).

## System Message Mode

`EXCELMANUS_SYSTEM_MESSAGE_MODE` (default `auto`):

- `replace`: Multiple system segments injected separately.
- `merge`: Merged into a single system message.
- `auto`: Defaults to `replace`; automatically falls back to `merge` when encountering provider multi-system compatibility errors.

## Multi-Model & AUX Model

> **Note**: The `EXCELMANUS_MODELS` environment variable is deprecated. Multi-model profiles have been migrated to database management via the Web settings page or `/model` command. On first launch, if this env var exists it will be auto-migrated to the database.

- `/model <name>` switches the main conversation model.
- When `EXCELMANUS_AUX_MODEL` is not set, the routing and window advisor models follow `/model` switching.
- When `EXCELMANUS_AUX_MODEL` is set, routing + subagent default model + window advisor model all use AUX, unaffected by `/model`.

## Window Perception Layer Configuration

| Environment Variable | Description | Default |
|---|---|---|
| `EXCELMANUS_WINDOW_PERCEPTION_ENABLED` | Enable window perception layer | `true` |
| `EXCELMANUS_WINDOW_PERCEPTION_SYSTEM_BUDGET_TOKENS` | System-injected window token budget | `3000` |
| `EXCELMANUS_WINDOW_PERCEPTION_TOOL_APPEND_TOKENS` | Tool return append budget | `500` |
| `EXCELMANUS_WINDOW_PERCEPTION_MAX_WINDOWS` | Maximum number of windows | `6` |
| `EXCELMANUS_WINDOW_PERCEPTION_DEFAULT_ROWS` | Default viewport rows | `25` |
| `EXCELMANUS_WINDOW_PERCEPTION_DEFAULT_COLS` | Default viewport columns | `10` |
| `EXCELMANUS_WINDOW_PERCEPTION_MINIMIZED_TOKENS` | Minimized window token budget | `80` |
| `EXCELMANUS_WINDOW_PERCEPTION_BACKGROUND_AFTER_IDLE` | Idle turns before entering background | `2` |
| `EXCELMANUS_WINDOW_PERCEPTION_SUSPEND_AFTER_IDLE` | Idle turns before entering suspended state | `5` |
| `EXCELMANUS_WINDOW_PERCEPTION_TERMINATE_AFTER_IDLE` | Idle turns before termination | `8` |
| `EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_MODE` | Lifecycle advisor mode (`rules`/`hybrid`) | `rules` |
| `EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_TIMEOUT_MS` | Small-model advisor timeout (milliseconds) | `800` |
| `EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_TRIGGER_WINDOW_COUNT` | Window count threshold to trigger small model | `3` |
| `EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_TRIGGER_TURN` | Conversation turn threshold to trigger small model | `4` |
| `EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_PLAN_TTL_TURNS` | Small-model plan TTL (turns) | `2` |

Advisor modes:
- `rules`: Uses deterministic rules only (no small-model calls).
- `hybrid`: Rules as fallback + async small-model caching; automatically falls back to rules on failure or timeout, without blocking the main pipeline.

### Window Perception Advanced Configuration

| Environment Variable | Description | Default |
|---|---|---|
| `EXCELMANUS_WINDOW_RETURN_MODE` | Tool return mode (`unified`/`anchored`/`enriched`/`adaptive`) | `adaptive` |
| `EXCELMANUS_ADAPTIVE_MODEL_MODE_OVERRIDES` | Per-model return mode overrides in adaptive mode (JSON object) | empty |
| `EXCELMANUS_WINDOW_FULL_MAX_ROWS` | Full window maximum rows | `25` |
| `EXCELMANUS_WINDOW_FULL_TOTAL_BUDGET_TOKENS` | Full window token budget | `500` |
| `EXCELMANUS_WINDOW_DATA_BUFFER_MAX_ROWS` | Data buffer maximum rows | `200` |
| `EXCELMANUS_WINDOW_INTENT_ENABLED` | Enable intent recognition | `true` |
| `EXCELMANUS_WINDOW_INTENT_STICKY_TURNS` | Intent sticky turns | `3` |
| `EXCELMANUS_WINDOW_INTENT_REPEAT_WARN_THRESHOLD` | Repeated intent warning threshold | `2` |
| `EXCELMANUS_WINDOW_INTENT_REPEAT_TRIP_THRESHOLD` | Repeated intent circuit-breaker threshold | `3` |
| `EXCELMANUS_WINDOW_RULE_ENGINE_VERSION` | Window rule engine version (`v1`/`v2`) | `v1` |

## VLM (Vision Language Model) Configuration

Supports image recognition and vision-enhanced descriptions. VLM model can be configured independently; falls back to the main model if not configured.

| Environment Variable | Description | Default |
|---|---|---|
| `EXCELMANUS_VLM_API_KEY` | VLM API Key (optional) | — |
| `EXCELMANUS_VLM_BASE_URL` | VLM Base URL (optional) | — |
| `EXCELMANUS_VLM_MODEL` | VLM model name (optional) | — |
| `EXCELMANUS_VLM_TIMEOUT_SECONDS` | VLM request timeout (seconds) | `300` |
| `EXCELMANUS_VLM_MAX_RETRIES` | VLM maximum retries | `1` |
| `EXCELMANUS_VLM_RETRY_BASE_DELAY_SECONDS` | VLM retry base delay (seconds) | `5.0` |
| `EXCELMANUS_VLM_IMAGE_MAX_LONG_EDGE` | Image long edge limit (px) | `2048` |
| `EXCELMANUS_VLM_IMAGE_JPEG_QUALITY` | JPEG compression quality | `92` |
| `EXCELMANUS_VLM_ENHANCE` | VLM enhanced description master switch | `true` |
| `EXCELMANUS_VLM_MAX_TOKENS` | VLM maximum output tokens | `16384` |
| `EXCELMANUS_VLM_PIPELINE_UNCERTAINTY_THRESHOLD` | Progressive pipeline uncertainty item threshold (pauses when exceeded) | `5` |
| `EXCELMANUS_VLM_PIPELINE_UNCERTAINTY_CONFIDENCE_FLOOR` | Pauses when any item falls below this confidence | `0.3` |
| `EXCELMANUS_VLM_PIPELINE_CHUNK_CELL_THRESHOLD` | Partitioned extraction when estimated cells exceed this value | `500` |
| `EXCELMANUS_MAIN_MODEL_VISION` | Main model vision capability (`auto`/`true`/`false`) | `auto` |

## Backup Sandbox Configuration

Enabled by default. All file write operations automatically retain copies in `outputs/backups/`, supporting rollback.

| Environment Variable | Description | Default |
|---|---|---|
| `EXCELMANUS_BACKUP_ENABLED` | Enable backup sandbox | `true` |

## Code Policy Engine Configuration

Performs static analysis on code executed by `run_code`, automatically routing approval by security level.

| Environment Variable | Description | Default |
|---|---|---|
| `EXCELMANUS_CODE_POLICY_ENABLED` | Enable code policy engine | `true` |
| `EXCELMANUS_CODE_POLICY_GREEN_AUTO` | Auto-approve Green-level (safe) code | `true` |
| `EXCELMANUS_CODE_POLICY_YELLOW_AUTO` | Auto-approve Yellow-level (audit-required) code | `true` |
| `EXCELMANUS_CODE_POLICY_EXTRA_SAFE` | Extra safe module allowlist (comma-separated) | empty |
| `EXCELMANUS_CODE_POLICY_EXTRA_BLOCKED` | Extra blocked module blocklist (comma-separated) | empty |

## Embedding Semantic Search Configuration

Provides semantic search capabilities for persistent memory and file manifests. Requires independent embedding API configuration; automatically enabled once configured.

| Environment Variable | Description | Default |
|---|---|---|
| `EXCELMANUS_EMBEDDING_ENABLED` | Enable semantic search (auto-enabled when API is configured) | `false` |
| `EXCELMANUS_EMBEDDING_API_KEY` | Embedding API Key | — |
| `EXCELMANUS_EMBEDDING_BASE_URL` | Embedding API Base URL | — |
| `EXCELMANUS_EMBEDDING_MODEL` | Embedding model name | `text-embedding-v3` |
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
| `EXCELMANUS_MEMORY_AUTO_EXTRACT_INTERVAL` | Background silent memory extraction every N turns (0 = disabled) | `15` |

Topic files: `file_patterns.md`, `user_prefs.md`, `error_solutions.md`, `general.md`.
The core file `MEMORY.md` is synced to topic files on save, used for automatic loading at session startup.

When Embedding is enabled, memory retrieval automatically switches to semantic matching mode.

## MCP Configuration

The project root `mcp.json` uses launchers from `scripts/mcp/*.sh`:

- On first launch, automatically installs to `./.excelmanus/mcp/` at a pinned version
- Subsequent launches reuse the local cache
- To force reinstall, delete `./.excelmanus/mcp/` and restart
- Use `EXCELMANUS_MCP_STATE_DIR` to customize the cache directory

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
| `EXCELMANUS_DATABASE_URL` | PostgreSQL connection URL (takes priority over `DB_PATH` when set) | empty |

## Chat History Persistence

| Environment Variable | Description | Default |
|---|---|---|
| `EXCELMANUS_CHAT_HISTORY_ENABLED` | Enable chat history persistence | `true` |

## Text Reply Guard Mode

Controls whether the Agent is intercepted and forced to continue execution when it "only replies with text without performing operations."

| Environment Variable | Description | Default |
|---|---|---|
| `EXCELMANUS_GUARD_MODE` | `off` (default, completely disables execution guard and write gate) / `soft` (retains guard but downgrades to diagnostic events only) | `off` |

## Tool Parameter Schema Validation

Performs JSON Schema-level validation on tool call parameters returned by the LLM, with three modes.

| Environment Variable | Description | Default |
|---|---|---|
| `EXCELMANUS_TOOL_SCHEMA_VALIDATION_MODE` | `off` (disabled) / `shadow` (log only, no blocking) / `enforce` (block and return error) | `off` |
| `EXCELMANUS_TOOL_SCHEMA_VALIDATION_CANARY_PERCENT` | `enforce` mode canary percentage (0~100), 100 = full rollout | `100` |
| `EXCELMANUS_TOOL_SCHEMA_STRICT_PATH` | Strict path policy: path parameters must be relative and forbid `..` | `false` |

## Turn Checkpoint

| Environment Variable | Description | Default |
|---|---|---|
| `EXCELMANUS_CHECKPOINT_ENABLED` | Auto-snapshot modified files after each tool call turn, supporting per-turn rollback | `false` |

## Docker Sandbox

| Environment Variable | Description | Default |
|---|---|---|
| `EXCELMANUS_DOCKER_SANDBOX` | Enable Docker sandbox isolation (requires pre-built image) | `false` |

## Thinking (Reasoning Depth)

| Environment Variable | Description | Default |
|---|---|---|
| `EXCELMANUS_THINKING_EFFORT` | Reasoning depth level (`none`/`minimal`/`low`/`medium`/`high`/`xhigh`) | `medium` |
| `EXCELMANUS_THINKING_BUDGET` | Exact token budget (overrides effort calculation when > 0) | `0` |

## OpenAI Responses API

| Environment Variable | Description | Default |
|---|---|---|
| `EXCELMANUS_USE_RESPONSES_API` | Set to `1` to enable Responses API (`/responses` endpoint), only effective for non-Gemini/Claude OpenAI-compatible URLs | `0` |

## Authentication & Multi-User

| Environment Variable | Description | Default |
|---|---|---|
| `EXCELMANUS_AUTH_ENABLED` | Enable authentication middleware (forces all API requests to carry JWT token) | `false` |
| `EXCELMANUS_SESSION_ISOLATION` | Session user isolation (requires auth to be enabled first; admin can enable at runtime via API) | `false` |
| `EXCELMANUS_JWT_SECRET` | JWT signing secret (auto-generated on each restart if empty; must be fixed in production) | Auto-generated |

### OAuth Login (Optional)

Requires creating an OAuth App on the corresponding platform.

| Environment Variable | Description |
|---|---|
| `EXCELMANUS_GITHUB_CLIENT_ID` | GitHub OAuth Client ID |
| `EXCELMANUS_GITHUB_CLIENT_SECRET` | GitHub OAuth Client Secret |
| `EXCELMANUS_GITHUB_REDIRECT_URI` | GitHub OAuth callback URL |
| `EXCELMANUS_GOOGLE_CLIENT_ID` | Google OAuth Client ID |
| `EXCELMANUS_GOOGLE_CLIENT_SECRET` | Google OAuth Client Secret |
| `EXCELMANUS_GOOGLE_REDIRECT_URI` | Google OAuth callback URL |
| `EXCELMANUS_OAUTH_PROXY` | OAuth proxy (needed for China servers to access Google) |

### Email Verification (Optional)

| Environment Variable | Description | Default |
|---|---|---|
| `EXCELMANUS_EMAIL_VERIFY_REQUIRED` | Require email verification on registration | `false` |
| `EXCELMANUS_EMAIL_FROM` | Sender display name + address | `ExcelManus <no-reply@yourdomain.com>` |
| `EXCELMANUS_RESEND_API_KEY` | Resend API Key (method 1, recommended) | — |
| `EXCELMANUS_SMTP_HOST` | SMTP server (method 2) | — |
| `EXCELMANUS_SMTP_PORT` | SMTP port (465 = SSL, 587 = STARTTLS) | — |
| `EXCELMANUS_SMTP_USER` | SMTP username | — |
| `EXCELMANUS_SMTP_PASSWORD` | SMTP password | — |

## Workspace Quota

Recommended for public-facing deployments to prevent a single user from consuming excessive resources.

| Environment Variable | Description | Default |
|---|---|---|
| `EXCELMANUS_WORKSPACE_MAX_SIZE_MB` | Maximum storage per user workspace (MB); uploads rejected when exceeded | `100` |
| `EXCELMANUS_WORKSPACE_MAX_FILES` | Maximum files per user workspace; oldest files auto-deleted when exceeded | `1000` |
