<p align="center">
  <img src="logo.svg" width="320" alt="ExcelManus" />
</p>

<p align="center">
  <strong>v1.5.7</strong> · Operate Excel with natural language — read data, write formulas, run analysis, create charts, all in one sentence.
</p>

<p align="center">
  <a href="README.md">中文</a> · English
</p>

ExcelManus is an LLM-powered intelligent agent for Excel. No need to memorize function syntax or write VBA — just tell it what you want, and it handles the rest. Supports OpenAI, Claude, Gemini and other major models, with automatic provider detection from the API URL.

## What It Can Do

- **Read & Write Excel** — Read cells, write formulas, VLOOKUP, batch fill, automatic multi-sheet handling
- **Data Analysis** — Filter, sort, group aggregation, pivot tables; complex logic auto-generates Python scripts
- **Chart Generation** — Bar, line, pie charts and more; describe what you want, embed in Excel or export as image
- **Image Understanding** — Paste a table screenshot, it recognizes and extracts structured data; supports two-stage extraction (data + style) for higher fidelity
- **Cross-Sheet Operations** — Create / copy / rename worksheets, move data across sheets
- **File Version Management** — Unified version chain tracking (staging / audit / CoW), `/undo` to rollback instantly
- **Persistent Memory** — Remembers your preferences and common patterns across sessions; file / database dual backend
- **Skill Extensions** — Add domain knowledge via Skillpacks; one Markdown file = one skill
- **MCP Integration** — Connect external MCP Servers to extend tool capabilities
- **Subagent** — Large files or complex tasks are automatically delegated to sub-agents
- **Multi-User Isolation** — Independent workspace, database, and sessions per user; auth = isolation
- **Admin Panel** — User management, model permission assignment, usage tracking

## Quick Start

**Install** (Python >= 3.10)

```bash
pip install .
```

**Configure** — Create a `.env` file in the project root with just 3 lines:

```dotenv
EXCELMANUS_API_KEY=your-api-key
EXCELMANUS_BASE_URL=https://your-llm-endpoint/v1
EXCELMANUS_MODEL=your-model-id
```

Works with any OpenAI-compatible API. If the URL points to Anthropic or Google, it automatically switches to the native Claude / Gemini protocol.

> `.env` is only used for initial configuration on first launch. After the first run, all settings are migrated to a local database and can be managed via the CLI `/config` command or the Web UI settings panel. In multi-user mode, global settings (model Profiles) are managed by admins, while user-level preferences (e.g., current model) are stored in each user's isolated database.

**Launch**

```bash
excelmanus            # CLI interactive mode
excelmanus-api        # REST API + Web UI backend
```

**Try It Out**

```
> Read the first 10 rows of sales.xlsx
> Sum column A amounts and write to B1
> Group sales by region and generate a bar chart
> Export the table in this screenshot to Excel @img screenshot.png
```

## Three Ways to Use

### CLI

Chat directly in the terminal. Supports Dashboard (split-pane) and Classic (chat) layouts.

Common commands:

| Command          | What It Does         |
| ---------------- | -------------------- |
| `/help`          | Help info            |
| `/skills`        | View / manage skills |
| `/model list`    | Switch models        |
| `/backup list`   | View backups         |
| `/undo <id>`     | Rollback operation   |
| `/rules`         | Custom rules         |
| `/memory`        | Persistent memory    |
| `/compact`       | Context compaction   |
| `/config export` | Encrypted config export |
| `/config import` | Import config token  |
| `/clear`         | Clear conversation   |
| `exit`           | Exit                 |

Auto-completion kicks in when you type `/`, and typo correction is built in. Full command list available via `/help`.

### Web UI

ExcelManus ships with a full web interface built on Next.js + Univer.js.

```bash
# Start backend
excelmanus-api

# Start frontend (install dependencies first)
cd web && npm install && npm run dev
```

Web UI features:

- Real-time chat (SSE streaming, live display of thinking process and tool calls)
- Pipeline progress bar — Connect → Route → Context Build → Tool Execution, each step visualized
- Embedded Excel viewer — Preview and edit spreadsheets in the side panel, with range selection reference
- Change tracking — Real-time diff display after each operation
- Multi-session management — Auto-save conversation history, switch anytime
- Settings panel — Model config, skill management, MCP Servers, memory, rules, all visual
- Admin panel — User management, role assignment, model permission control, usage stats
- Config sharing — One-click export of all model configs (including API Keys), encrypted for sharing
- Approval workflow — High-risk operations require confirmation popup, one-click undo
- File drag & drop — Drag files directly into the input box
- `@` mentions — Type `@` to reference files, tools, or skills, with line range support (e.g., `@file.py:10-20`)

### REST API

```bash
excelmanus-api
```

Core endpoints:

| Endpoint                             | Description              |
| ------------------------------------ | ------------------------ |
| `POST /api/v1/chat/stream`           | SSE streaming chat       |
| `POST /api/v1/chat`                  | Full JSON chat           |
| `POST /api/v1/chat/abort`            | Abort running task       |
| `GET /api/v1/files/excel`            | Excel file stream        |
| `GET /api/v1/files/excel/snapshot`   | Lightweight Excel JSON snapshot |
| `POST /api/v1/backup/apply`          | Apply backup to original file |
| `GET /api/v1/skills`                 | Skill list               |
| `POST /api/v1/skills`               | Create / import skill    |
| `POST /api/v1/config/export`         | Encrypted config export  |
| `POST /api/v1/config/import`         | Import config token      |
| `GET /api/v1/health`                 | Health check             |

SSE pushes 25 event types covering thinking process, tool calls, sub-agent execution, pipeline progress, Excel preview / diff, approval requests, memory extraction, and more.

## Model Support

| Provider             | Notes                                                     |
| -------------------- | --------------------------------------------------------- |
| OpenAI Compatible    | Default protocol, works with all OpenAI API-compatible services |
| Claude (Anthropic)   | Auto-switches when URL contains `anthropic`, supports extended thinking |
| Gemini (Google)      | Auto-switches when URL contains `googleapis` / `generativelanguage` |
| OpenAI Responses API | Enable with `EXCELMANUS_USE_RESPONSES_API=1`              |

Switch models at runtime via the `/model` command or Web UI. An auxiliary model (AUX) can be configured for routing decisions, sub-agent execution, and window lifecycle management.

## Config Sharing

Export all model configurations (main model / auxiliary model / VLM / multi-model Profiles, including API Keys) as an encrypted token that others can import directly.

**Two encryption modes:**

| Mode | Security | Description |
| ---- | -------- | ----------- |
| Passphrase (default) | High | AES-256-GCM + PBKDF2, cannot decrypt without password |
| Simple sharing | Medium | Built-in key, no password needed, suitable for trusted environments |

**Usage:**

```bash
# CLI
/config export                       # Passphrase encryption (interactive password input)
/config export --simple              # Simple sharing mode
/config import EMX1:P:xxxx...        # Import token

# Web UI
# Settings → Model Config → "Config Export/Import" panel at the bottom
```

You can select which config sections to include when exporting, and passwords support one-click random generation. Token format is `EMX1:<P|S>:<base64>`, shareable via chat, email, or any channel.

## Security

ExcelManus applies multiple layers of protection for file operations and code execution:

- **Path Sandbox** — All reads/writes restricted to the working directory; path traversal and symlink escapes are rejected
- **Code Policy Engine** — Static analysis before `run_code` execution, auto-approval by Green / Yellow / Red tiers
- **Docker Sandbox** — Optional Docker container isolation for user code execution (`EXCELMANUS_DOCKER_SANDBOX=1`)
- **Operation Approval** — High-risk writes require user `/accept` confirmation; all auditable operations record change diffs and snapshots
- **File Version Management** — Unified version chain (staging / audit / CoW), `/undo` rollback to any version
- **MCP Tool Whitelist** — External MCP tools require confirmation by default, configurable auto-approve
- **User Isolation** — Physical workspace and database isolation per user in multi-user mode

## Skillpack Extensions

Skillpacks let you inject domain knowledge into the agent without changing code.

Create a directory with a `SKILL.md` file containing `name` and `description`, and ExcelManus will auto-discover and activate it at the right time. Supports Hooks (intercept tool calls), command dispatch, MCP dependency declarations, and other advanced features.

Built-in skills:

| Skill                 | Purpose                                |
| --------------------- | -------------------------------------- |
| `data_basic`          | Read, analyze, filter & transform      |
| `chart_basic`         | Chart generation (Excel embedded + standalone images) |
| `format_basic`        | Style adjustments, conditional formatting |
| `file_ops`            | File management                        |
| `sheet_ops`           | Worksheet management & cross-sheet ops |
| `excel_code_runner`   | Process large files with Python scripts |
| `run_code_templates`  | Common code template library for run_code |

Protocol details in `docs/skillpack_protocol.md`.

## Bench Testing

Built-in automated evaluation framework for batch-validating agent performance:

```bash
python -m excelmanus.bench --all                         # Run all
python -m excelmanus.bench --suite bench/cases/xxx.json  # Specific suite
python -m excelmanus.bench --message "Read first 10 rows"  # Single test
```

Supports multi-turn conversation cases, automatic assertion validation, structured JSON logs, `--trace` engine internals, and suite-level concurrent execution.

## Deployment

### Docker Compose (Recommended)

```bash
cp .env.example .env
# Edit .env to configure API Key, model, etc.

docker compose up -d                          # Start (backend + frontend + PostgreSQL)
docker compose --profile production up -d     # With Nginx reverse proxy
```

Access `http://localhost` (Nginx) or `http://localhost:3000` (direct frontend) after startup.

### Manual Deployment (BT Panel / Bare Metal)

For non-Docker scenarios, see [docs/ops-manual.md](docs/ops-manual.md).

### One-Click Update

The `deploy.sh` script syncs code and restarts the remote server from your local machine:

```bash
./deploy.sh                  # Full deploy (backend + frontend)
./deploy.sh --backend-only   # Backend only (fastest)
./deploy.sh --frontend-only  # Frontend only
./deploy.sh --skip-build     # Skip frontend build, restart only
./deploy.sh --frontend-only --frontend-artifact ./web-dist/frontend-standalone.tar.gz  # Use local/CI frontend artifact (recommended for low-memory servers)
./deploy.sh --frontend-only --cold-build  # Remote cold build (troubleshooting only)
```

> The script automatically excludes `.env`, `data/`, `workspace/` and other data directories, so it won't overwrite server configs or user data.

### Deployment Notes

#### Next.js Standalone Static Assets

When building the frontend in Next.js standalone mode, `public/` and `.next/static/` are not automatically copied to the `.next/standalone/` output directory. You need to copy them manually:

```bash
cd web
npm run build
cp -r public .next/standalone/
cp -r .next/static .next/standalone/.next/
```

Otherwise, logos, images, CSS, and other static assets will return 404 or 500 errors. Automate this step in `deploy.sh`.

#### Low-Memory Server Publishing (Strongly Recommended)

For servers with limited memory (1–2 GB), avoid running `npm run build` directly on the server. Recommended workflow:

1. Build locally or in CI: `cd web && npm run build` (use `npm run build:webpack` as fallback if needed).
2. Package the minimal runtime set (`.next/standalone`, `.next/static`, `public`):

   ```bash
   cd web
   tar -czf ../web-dist/frontend-standalone.tar.gz .next/standalone .next/static public
   ```

3. Deploy with `./deploy.sh --frontend-only --frontend-artifact <tar.gz>` for atomic switching.

If remote building is unavoidable, keep `.next/cache` and only use `--cold-build` for explicit troubleshooting.

#### Nginx SSE Streaming Configuration

If the frontend accesses the backend through Nginx, configure SSE endpoints separately to prevent response buffering:

```nginx
# SSE streaming endpoint
location /api/v1/chat/stream {
    proxy_pass http://backend-server:8000;
    proxy_http_version 1.1;
    proxy_set_header Connection '';  # Key: clear Connection header (SSE doesn't need upgrade)
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 600s;
    chunked_transfer_encoding on;
}

# Other API requests
location /api/ {
    proxy_pass http://backend-server:8000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection 'upgrade';  # For WebSocket
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 300s;
}
```

**Key point**: SSE (Server-Sent Events) is not WebSocket and doesn't need `Connection: upgrade`. Setting `Connection: upgrade` for all `/api/` requests will cause SSE connections to fail, manifesting as "fail to fetch" errors when sending messages.

## Multi-User & Authentication

ExcelManus supports multi-user mode. Enabling authentication automatically enables user isolation (auth = isolation), with each user getting an independent workspace directory and database.

```dotenv
EXCELMANUS_AUTH_ENABLED=true
EXCELMANUS_JWT_SECRET=your-random-secret-key-at-least-64-chars
```

Three login methods:

- **Email + Password** — Register and use directly
- **GitHub OAuth** — Create an OAuth App in [GitHub Developer Settings](https://github.com/settings/developers)
- **Google OAuth** — Create OAuth credentials in [Google Cloud Console](https://console.cloud.google.com)

OAuth configuration:

```dotenv
# GitHub
EXCELMANUS_GITHUB_CLIENT_ID=your-client-id
EXCELMANUS_GITHUB_CLIENT_SECRET=your-client-secret
EXCELMANUS_GITHUB_REDIRECT_URI=https://your-domain/api/v1/auth/oauth/github/callback

# Google
EXCELMANUS_GOOGLE_CLIENT_ID=your-client-id
EXCELMANUS_GOOGLE_CLIENT_SECRET=your-client-secret
EXCELMANUS_GOOGLE_REDIRECT_URI=https://your-domain/api/v1/auth/oauth/google/callback

# Proxy for accessing Google API from China
# EXCELMANUS_OAUTH_PROXY=socks5://127.0.0.1:1080
```

In multi-user mode, each user has an independent workspace, isolated SQLite database (`users/{user_id}/data.db`), conversation history, and token usage tracking. The first registered user automatically becomes admin. Admins can assign user roles and model permissions in the Web UI admin panel.

## Configuration Reference

Quick start requires only 3 environment variables. For fine-tuning, ExcelManus offers extensive configuration options covering window perception, security policies, subagent, MCP, VLM, embedding semantic search, and more.

Full configuration docs: [docs/configuration.md](docs/configuration.md)

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

MIT
