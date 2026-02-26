<p align="center">
  <img src="logo.svg" width="320" alt="ExcelManus" />
</p>

<p align="center">
  <strong>v1.6.0</strong> · Operate Excel with natural language
</p>

<p align="center">
  <a href="README.md">中文</a> · English
</p>

LLM-powered Excel Agent — read data, write formulas, run analysis, create charts. Supports OpenAI / Claude / Gemini, auto-detects provider from URL.

<p align="center">
  <img src="docs/images/webui-desktop.png" width="800" alt="Web UI Desktop" />
</p>
<p align="center">Desktop Web UI — Chat + Excel side panel real-time preview</p>

<p align="center">
  <img src="docs/images/webui-mobile.png" width="360" alt="Web UI Mobile" />
</p>
<p align="center">Mobile — Conversational interaction, tool calls and data changes at a glance</p>

## Features

- **Read & Write Excel** — Cells, formulas, VLOOKUP, batch fill, multi-sheet
- **Data Analysis** — Filter, sort, aggregation, pivot tables; complex logic generates Python scripts
- **Charts** — Bar, line, pie charts, embed in Excel or export as image
- **Image Recognition** — Table screenshot → structured data, supports data + style two-stage extraction
- **Cross-Sheet Operations** — Create / copy / rename sheets, move data across sheets
- **Version Management** — staging / audit / CoW version chain, `/undo` rollback
- **Persistent Memory** — Remembers preferences and patterns across sessions
- **Skillpack** — One Markdown file = one skill, inject domain knowledge
- **MCP** — Connect external MCP Servers to extend tools
- **Subagent** — Large files or complex tasks delegated to sub-agents
- **Multi-User** — Independent workspace / database / sessions, admin panel controls permissions and usage

## Quick Start

**Install** (Python >= 3.10)

```bash
pip install .
```

Create `.env`:

```dotenv
EXCELMANUS_API_KEY=your-api-key
EXCELMANUS_BASE_URL=https://your-llm-endpoint/v1
EXCELMANUS_MODEL=your-model-id
```

Works with any OpenAI-compatible API. Auto-switches to native Claude / Gemini protocol for Anthropic / Google URLs.

> After first run, settings migrate to local database. Manage via `/config` or Web UI.

```bash
excelmanus            # CLI
excelmanus-api        # REST API + Web UI backend
```

```
> Read the first 10 rows of sales.xlsx
> Sum column A amounts and write to B1
> Group sales by region and generate a bar chart
```

## Usage

### CLI

Terminal chat, supports Dashboard and Classic layouts.

| Command | Description |
| --- | --- |
| `/help` | Help |
| `/skills` | Skill management |
| `/model list` | Switch models |
| `/undo <id>` | Rollback operation |
| `/backup list` | View backups |
| `/rules` | Custom rules |
| `/memory` | Memory management |
| `/compact` | Context compaction |
| `/config export` | Encrypted config export |
| `/config import` | Import config |
| `/clear` | Clear conversation |

Auto-completion on `/`, typo correction built-in.

### Web UI

Built on Next.js + Univer.js.

```bash
excelmanus-api                          # Backend
cd web && npm install && npm run dev    # Frontend
```

- SSE streaming, live thinking process and tool calls
- Embedded Excel viewer, side panel preview/edit, range selection support
- Real-time diff on write operations
- Multi-session, settings panel, admin panel
- File drag & drop, `@` reference files / skills
- High-risk operation approval confirmation

### REST API

Available after `excelmanus-api` starts.

| Endpoint | Description |
| --- | --- |
| `POST /api/v1/chat/stream` | SSE streaming chat |
| `POST /api/v1/chat` | JSON chat |
| `POST /api/v1/chat/abort` | Abort task |
| `GET /api/v1/files/excel` | Excel file stream |
| `GET /api/v1/files/excel/snapshot` | Excel JSON snapshot |
| `POST /api/v1/backup/apply` | Apply backup |
| `GET /api/v1/skills` | Skill list |
| `POST /api/v1/config/export` | Export config |
| `GET /api/v1/health` | Health check |

SSE pushes 25 event types (thinking, tool calls, subagents, Excel diff, approval, etc.).

## Models

| Provider | Description |
| --- | --- |
| OpenAI Compatible | Default protocol |
| Claude (Anthropic) | Auto-switches when URL contains `anthropic`, supports extended thinking |
| Gemini (Google) | Auto-switches when URL contains `googleapis` / `generativelanguage` |
| OpenAI Responses API | Enable with `EXCELMANUS_USE_RESPONSES_API=1` |

Configure auxiliary model (AUX) for routing, sub-agents, and window management. Switch models at runtime via `/model` or Web UI.

## Security

- **Path Sandbox** — Reads/writes restricted to working directory, path traversal and symlink escapes rejected
- **Code Static Analysis** — `run_code` auto-approval by Green / Yellow / Red tiers
- **Docker Sandbox** — Optional container isolation (`EXCELMANUS_DOCKER_SANDBOX=1`)
- **Operation Approval** — High-risk writes require `/accept` confirmation, changes record diffs and snapshots
- **Version Chain** — staging / audit / CoW, `/undo` rollback to any version
- **MCP Whitelist** — External tools require confirmation by default
- **User Isolation** — Physical workspace and database isolation per user

## Skillpack

One directory + one `SKILL.md` (with `name` and `description`) is all you need. Auto-discovery and on-demand activation. Supports Hooks, command dispatch, MCP dependency declarations.

Built-in skills:

| Skill | Purpose |
| --- | --- |
| `data_basic` | Read, analyze, filter, transform |
| `chart_basic` | Charts (embedded + images) |
| `format_basic` | Styles, conditional formatting |
| `file_ops` | File management |
| `sheet_ops` | Worksheet & cross-sheet operations |
| `excel_code_runner` | Python scripts for large files |
| `run_code_templates` | Common code templates |

Protocol details in `docs/skillpack_protocol.md`.

## Bench

Built-in evaluation framework:

```bash
python -m excelmanus.bench --all                         # All
python -m excelmanus.bench --suite bench/cases/xxx.json  # Specific suite
python -m excelmanus.bench --message "Read first 10 rows"  # Single test
```

Supports multi-turn cases, auto-assertion, JSON logs, `--trace` engine internals, suite-level concurrency.

## Deployment

### Docker Compose (Recommended)

```bash
cp .env.example .env
# Edit .env for API Key, model, etc.

docker compose up -d                          # Start (backend + frontend + PostgreSQL)
docker compose --profile production up -d     # With Nginx reverse proxy
```

Access `http://localhost` (Nginx) or `http://localhost:3000` (direct frontend).

### Manual Deployment (BT Panel / Bare Metal)

For non-Docker scenarios, see [docs/ops-manual.md](docs/ops-manual.md).

### Remote Update

```bash
./deploy.sh                  # Full deploy
./deploy.sh --backend-only   # Backend only
./deploy.sh --frontend-only  # Frontend only
```

> Automatically excludes `.env`, `data/`, `workspace/`, won't overwrite server data. Low-memory servers, Nginx SSE config, see [docs/ops-manual.md](docs/ops-manual.md).

## Multi-User

```dotenv
EXCELMANUS_AUTH_ENABLED=true
EXCELMANUS_JWT_SECRET=your-random-secret-key-at-least-64-chars
```

Supports email/password, GitHub OAuth, Google OAuth login. Each user has independent workspace and database (`users/{user_id}/data.db`). First registered user becomes admin.

OAuth and other detailed configuration in [docs/configuration.md](docs/configuration.md).

## Configuration Reference

Quick start needs only 3 environment variables. Full configuration (window perception, security policies, Subagent, MCP, VLM, Embedding, etc.) in [docs/configuration.md](docs/configuration.md).

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

MIT
