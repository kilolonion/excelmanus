<p align="center">
  <img src="web/public/logo.svg" width="380" alt="ExcelManus" />
</p>

<h3 align="center">AI Agent that operates Excel with natural language</h3>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue.svg" alt="License" /></a>
  <img src="https://img.shields.io/badge/python-≥3.10-3776AB.svg?logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/version-1.6.8-green.svg" alt="Version" />
  <img src="https://img.shields.io/badge/Next.js-16-black?logo=next.js" alt="Next.js" />
</p>

<p align="center">
  <a href="README.md">中文</a> · English · <a href="docs/configuration_en.md">Configuration</a> · <a href="docs/ops-manual.md">Ops Manual</a>
</p>

<p align="center">
  <img src="docs/images/webui-desktop.png" width="720" alt="Web UI" />
</p>

---

ExcelManus is an LLM-powered Excel Agent framework. Tell it what you want — it reads data, writes formulas, runs analysis, and creates charts automatically. Supports both CLI and Web interfaces, works with OpenAI / Claude / Gemini and any compatible LLM.

## ✨ Key Features

<table>
<tr>
<td width="50%">

### 📊 Read & Write Excel
Cells · Formulas · VLOOKUP · Batch fill · Multi-sheet operations; auto-converts `.xlsx` / `.xls` / `.xlsb` / `.csv`

### 📈 Data Analysis & Charts
Filter, sort, aggregate, pivot tables; complex logic auto-generates Python scripts. Bar, line, pie charts embedded in Excel or exported as images.

### 🖼️ Vision Extraction
Table screenshot → structured data; 4-stage progressive pipeline (skeleton→data→style→formula), supports single-pass extraction and large-table chunking

### 🔄 Version Management & Diff
Staging / Audit / CoW version chain, `/undo` precise rollback; Excel write diff visualization; text file precision editing + unified diff display

</td>
<td width="50%">

### 🧠 Persistent Memory & Playbook
Cross-session memory for preferences; Playbook automatically distills task experience, reuses lessons in future tasks

### 🧩 Skillpack & ClawHub Market
One Markdown = one skill. Auto-discovery, on-demand activation; built-in [ClawHub](https://clawhub.ai) market for one-click search/install/update

### 🔌 MCP & Subagent
Connect external MCP Servers to extend toolset; large files and complex tasks auto-delegated to sub-agents; supports OpenAI Codex subscription

### ✅ Verification Gate
Structured verification conditions (row count / sheet exists / formula / value match), auto-validates before task completion, blocks tasks with failing conditions

### 📤 Session Export & History
Export to Markdown / Plain Text / EMX (JSON); session history persisted (SQLite + IndexedDB 3-tier cache), survives refresh/restart

### 👥 Multi-User & Admin
Independent workspace / database / session isolation; admin panel with per-user/model LLM usage visualization; OAuth credential management

</td>
</tr>
</table>

## 🚀 Quick Start

### Option 1: One-Click Start Script (Recommended)

The easiest way — the script auto-installs dependencies and launches both backend and frontend.

**Step 1: Clone the project**

```bash
# Recommended for users in China (faster)
git clone https://gitee.com/kilolonion/excelmanus.git
# Or use GitHub
# git clone https://github.com/kilolonion/excelmanus.git
cd excelmanus
```

**Step 2: Run the start script**

```bash
# macOS / Linux
./deploy/start.sh

# Windows PowerShell
.\deploy\start.ps1

# Windows CMD
deploy\start.bat
```

The script will automatically:
- Detect and install Python, Node.js and other dependencies
- Install required Python packages and frontend modules

**Step 3: Enter your API Key when prompted**

On first launch, the script interactively asks for your LLM configuration (3 items):

```
  ========================================
    First Launch - Configure ExcelManus
  ========================================

  API Key: sk-xxxxxxxxxxxxx
  Base URL (e.g. https://api.openai.com/v1): https://your-llm-endpoint/v1
  Model (e.g. gpt-4o): gpt-4o
```

The script creates a `.env` config file and starts the service. **No need to re-enter on subsequent launches.**

Your browser will automatically open **http://localhost:3000** — the Web UI is ready to use.

<details>
<summary>Common start options</summary>

```bash
./deploy/start.sh --prod             # Production mode (better performance)
./deploy/start.sh --backend-port 9000  # Custom backend port
./deploy/start.sh --workers 4         # Multi-worker
./deploy/start.sh --backend-only      # Backend only (no frontend)
./deploy/start.sh --help              # All options
```

</details>

**Step 4: Start chatting**

Type natural language instructions in the Web UI or CLI:

```
> Read the first 10 rows of sales.xlsx
> Sum column A amounts and write to B1
> Group sales by region and generate a bar chart
```

> After first run, settings migrate to local database. Manage via Web UI settings panel or `/config` command.

---

### Option 2: Manual Install (uv, Recommended)

For users with an existing Python environment (≥3.10) who want precise control over dependencies. Uses [uv](https://docs.astral.sh/uv/) for 10-100x faster dependency management.

**1. Install uv (if not already installed)**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # macOS / Linux
# Windows: powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**2. Clone and install**

```bash
# Recommended for users in China (faster)
git clone https://gitee.com/kilolonion/excelmanus.git
# Or use GitHub
# git clone https://github.com/kilolonion/excelmanus.git
cd excelmanus
uv sync --all-extras          # Full install (CLI + Web + all optional deps)
# Or pick what you need:
# uv sync --extra cli          # CLI only (lightweight, no Web UI)
# uv sync --extra web          # Web API only (no CLI dashboard)
```

> Also supports traditional pip: `pip install ".[all]"`

**2. Create config file**

```bash
cp .env.example .env
```

Open `.env` in any editor and fill in the 3 required fields at the top:

```dotenv
EXCELMANUS_API_KEY=sk-xxxxxxxxxxxxxxxx        # Your LLM API Key
EXCELMANUS_BASE_URL=https://api.openai.com/v1  # Model endpoint URL
EXCELMANUS_MODEL=gpt-4o                        # Model name
```

> All other settings have sensible defaults. See [Configuration](docs/configuration_en.md) for the full reference.

**3. Launch**

```bash
uv run excelmanus            # CLI terminal mode
uv run excelmanus-api        # Web API mode (backend at http://localhost:8000)
```

For the Web UI frontend, start it separately:

```bash
cd web && npm install && npm run dev    # Frontend dev server (http://localhost:3000)
```

## 💻 Usage

### CLI

Terminal chat with Dashboard layout, `/` auto-completion, and typo correction.

<details>
<summary>📋 Common Commands</summary>

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
| `/export` | Export session (Markdown / Plain Text / EMX) |
| `/clear` | Clear conversation |

</details>

### Web UI

Built on Next.js + Univer.js, providing a full visual experience.

```bash
# Option 1: One-click start (recommended)
./deploy/start.sh

# Option 2: Start separately
excelmanus-api                          # Backend
cd web && npm install && npm run dev    # Frontend
```

- **SSE Streaming** — Real-time display of thinking process, tool calls, sub-agent execution; auto-reconnect with message recovery
- **Excel Side Panel** — Embedded Univer viewer, live preview/edit, range selection; quick file bar in sidebar with full-screen double-click mode
- **Excel & Text Diff** — Before/after comparison on every write; text files show unified diff with line numbers
- **Multi-Session** — Persistent history (SQLite + IndexedDB 3-tier cache), survives refresh/restart, seamless switching
- **File Interaction** — Drag & drop upload, `@` reference files and skills; `.xls` / `.xlsb` auto-converted
- **Approval Flow** — Confirmation dialog for high-risk operations, changes auto-snapshot
- **Optimistic UI** — Messages appear immediately, write operations optimistic update + rollback on failure
- **Error Guidance** — Actionable suggestion cards on failure (retry / check settings / copy diagnostic ID)
- **ClawHub Market** — Inline skill market panel in sidebar for search/install/update
- **Admin Dashboard** — User management + per-provider/model LLM usage visualization
- **Plan Mode** — Complex tasks auto-planned, interactive confirmation before execution

<p align="center">
  <img src="docs/images/webui-mobile.png" width="300" alt="Mobile" />
</p>
<p align="center"><sub>Mobile-friendly — responsive layout</sub></p>

### REST API

Available once `excelmanus-api` starts. SSE pushes 30+ event types (including `excel_diff` / `text_diff` / `failure_guidance`).

<details>
<summary>📋 Main Endpoints</summary>

| Endpoint | Description |
| --- | --- |
| `POST /api/v1/chat/stream` | SSE streaming chat |
| `POST /api/v1/chat` | JSON chat |
| `POST /api/v1/chat/abort` | Abort task |
| `POST /api/v1/chat/subscribe` | Reconnect and restore session stream |
| `POST /api/v1/chat/rollback` | Rollback session to a specific turn |
| `GET /api/v1/sessions` | Session list (with archive filter) |
| `GET /api/v1/sessions/{id}/messages` | Paginated message history |
| `GET /api/v1/files/excel` | Excel file stream (Univer load) |
| `GET /api/v1/files/excel/snapshot` | Excel JSON snapshot (inline preview) |
| `POST /api/v1/files/excel/write` | Side panel write-back |
| `POST /api/v1/backup/apply` | Apply backup |
| `GET /api/v1/skills` | Skill list |
| `GET /api/v1/clawhub/search` | ClawHub market search |
| `POST /api/v1/clawhub/install` | Install market skill |
| `GET /api/v1/clawhub/updates` | Check for updates |
| `GET /api/v1/auth/codex/status` | Codex connection status |
| `POST /api/v1/config/export` | Export config |
| `GET /api/v1/health` | Health check |

</details>

## 🤖 Model Support

| Provider | Description |
| --- | --- |
| **OpenAI Compatible** | Default protocol, works with any compatible API (local Ollama / vLLM etc.) |
| **Claude (Anthropic)** | Auto-switches when URL contains `anthropic`, supports extended thinking |
| **Gemini (Google)** | Auto-switches when URL contains `googleapis` / `generativelanguage` |
| **OpenAI Responses API** | Next-gen inference API, enable with `EXCELMANUS_USE_RESPONSES_API=1` |
| **OpenAI Codex Subscription** | Connect via Device Code Flow, private models auto-discovered, no manual API Key |
| **MiniMax** | Auto-detects base_url, built-in recommended model list (M2.5 / M2.1 / M2) |

### Auxiliary Model (AUX)

Configure an independent auxiliary model for **intent routing, sub-agents, and window perception advisor**:

```dotenv
EXCELMANUS_AUX_API_KEY=sk-xxxx
EXCELMANUS_AUX_BASE_URL=https://api.openai.com/v1
EXCELMANUS_AUX_MODEL=gpt-4o-mini
```

The auxiliary model can be lighter than the main model, significantly reducing cost without affecting task quality.

## 🔒 Security

| Mechanism | Description |
| --- | --- |
| **Path Sandbox** | Reads/writes restricted to working directory, path traversal and symlink escapes rejected |
| **Code Review** | `run_code` static analysis, Green / Yellow / Red tier auto-approval |
| **Docker Sandbox** | Optional container isolation (`EXCELMANUS_DOCKER_SANDBOX=1`) |
| **Operation Approval** | High-risk writes require confirmation, changes auto-record diffs and snapshots |
| **Version Chain** | Staging → Audit → CoW, `/undo` rollback to any version |
| **MCP Whitelist** | External tools require per-item confirmation by default |
| **User Isolation** | Physical workspace and database isolation per user in multi-user mode |

## 🧩 Skillpack & ClawHub

One directory + one `SKILL.md` (with `name` and `description`) to create a skill. Auto-discovery, on-demand activation, supports Hooks, command dispatch, and MCP dependency declarations.

### ClawHub Skill Market

Built-in [ClawHub](https://clawhub.ai) integration — search, install, and update community skill packs directly from the UI sidebar or CLI:

```bash
/clawhub search financial reports   # Search market
/clawhub install <slug>             # Install
/clawhub update                     # Update all installed
```

<details>
<summary>📦 Built-in Skills</summary>

| Skill | Purpose |
| --- | --- |
| `data_basic` | Read, analyze, filter, transform |
| `chart_basic` | Charts (embedded + images) |
| `format_basic` | Styles, conditional formatting |
| `file_ops` | File management |
| `sheet_ops` | Worksheet & cross-sheet operations |
| `excel_code_runner` | Python scripts for large files |
| `run_code_templates` | Common code templates |

</details>

Protocol details in [`docs/skillpack_protocol.md`](docs/skillpack_protocol.md).

## 🎯 Verification Gate

Add **structured verification conditions** to task subtasks — auto-validates before task completion:

```
> Import sales data into Sheet1, require: row count matches source, column B has SUM formula, C1 value is "Total"
```

Supported check types: `row_count` / `sheet_exists` / `formula_exists` / `value_match` / `custom`

Tasks with failing verification conditions **block finish_task**, forcing the agent to fix them first.

## 🧠 Playbook — Task Experience Learning

Playbook automatically analyzes success/failure patterns from each task and distills reusable operational knowledge:

- **Auto-learn**: Generates PlaybookDelta after task completion, stored in SQLite
- **Semantic dedup**: Similar entries merged, stale entries retired
- **Auto-inject**: Matching Playbook entries injected before related future tasks, reducing repeated mistakes
- **Management**: `/playbook list` to view, `/playbook clear` to reset

## 🏗️ Deployment

### Docker Compose (Recommended)

```bash
cp .env.example .env                      # Edit API Key, model, etc.
docker compose -f deploy/docker-compose.yml up -d   # Backend + Frontend + PostgreSQL
```

Visit `http://localhost:3000`. Add `--profile production` for Nginx reverse proxy at `http://localhost`.

### Manual Deployment

For BT Panel / bare metal scenarios without Docker, see [Ops Manual](docs/ops-manual.md).

### One-Click Start (Local Development)

```bash
# macOS / Linux
./deploy/start.sh              # Dev mode
./deploy/start.sh --prod       # Production mode (npm run start)
./deploy/start.sh --workers 4  # Multi-worker

# Windows PowerShell
.\deploy\start.ps1 -Production

# Windows CMD
deploy\start.bat --prod
```

Supports `--backend-port`, `--frontend-port`, `--log-dir`, `--backend-only`, `--frontend-only` and more. See `./deploy/start.sh --help` for details.

Scripts auto-detect OS (macOS / Linux / Windows) and on Linux identify apt / dnf / yum / pacman package managers with install hints.

### Remote Deploy (deploy.sh / deploy.ps1)

Deploy scripts run locally, operating remote servers via SSH. Supports single-server, split frontend/backend, Docker, and local topologies.

```bash
# Basic deployment
./deploy/deploy.sh                         # Full deploy
./deploy/deploy.sh --backend-only          # Backend only
./deploy/deploy.sh --frontend-only         # Frontend only

# Split servers with dual SSH keys
./deploy/deploy.sh --backend-host 1.2.3.4 --frontend-host 5.6.7.8 \
    --backend-key ~/.ssh/backend.pem --frontend-key ~/.ssh/frontend.pem

# Low-memory server: build frontend locally, upload artifact
./deploy/deploy.sh --frontend-only --frontend-artifact ./frontend-standalone.tar.gz

# First deploy: push .env template to remote servers
./deploy/deploy.sh init-env

# Operations
./deploy/deploy.sh check                   # Environment check + connectivity test
./deploy/deploy.sh status                  # View running status
./deploy/deploy.sh rollback                # Rollback to previous version
./deploy/deploy.sh history                 # Deploy history
./deploy/deploy.sh logs                    # Deploy logs
```

Windows PowerShell:

```powershell
.\deploy\deploy.ps1                        # Full deploy
.\deploy\deploy.ps1 init-env               # Push .env template
.\deploy\deploy.ps1 check                  # Environment check
.\deploy\deploy.ps1 rollback -Force        # Rollback (skip confirmation)
```

<details>
<summary>Deployment Safety</summary>

Three-layer protection to prevent 502 during deployment:

| Layer | Mechanism | Description |
| --- | --- | --- |
| **Build exit code** | No `\| tail` pipe | Prevents pipe from swallowing `npm run build` exit code |
| **Artifact validation** | Validates BUILD_ID + routes-manifest.json | Refuses restart if Turbopack build is incomplete |
| **Startup fallback** | Auto-detects standalone vs next start | Works regardless of whether Next.js generates standalone output |

On build failure or incomplete artifacts, the script aborts and keeps the current running version.

</details>

> Automatically excludes `.env`, `data/`, `workspace/` — won't overwrite server data. Post-deploy auto-checks frontend↔backend connectivity, CORS config, and health.

## 👥 Multi-User

```dotenv
EXCELMANUS_AUTH_ENABLED=true
EXCELMANUS_JWT_SECRET=your-random-secret-key-at-least-64-chars
```

Supports **email/password**, **GitHub OAuth**, **Google OAuth**, and **QQ OAuth** login. Each user gets an independent workspace and database. First registered user becomes admin.

### OpenAI Codex Subscription

Users can bind their OpenAI Codex subscription via **Device Code Flow** from the profile page:

1. Click "Connect Codex" → page displays a 6-digit verification code
2. Enter the code at [auth.openai.com/codex/device](https://auth.openai.com/codex/device)
3. On success, private Codex models are auto-discovered — no manual API Key needed

> Requires enabling "Enable device code authentication for Codex" in ChatGPT Settings → Security.

### Admin Dashboard

Admins can access `/admin` to:
- View all users' LLM usage (grouped by provider/model: calls / tokens / last used)
- Manage login method toggles (email registration / OAuth providers)
- Set model allowlists and system-level configuration

> **Split-server note**: Google/GitHub OAuth callbacks optimized to frontend page + browser-direct-to-backend token exchange, avoiding cross-server proxy chain timeouts. Set redirect URI to `https://your-domain/auth/callback` in the OAuth provider console.

See [Configuration](docs/configuration_en.md) for details.

## 🧪 Evaluation Framework

Built-in Bench evaluation with multi-turn cases, auto-assertion, JSON logs, and suite-level concurrency. Bench Reporter now includes **reasoning quality metrics** (silent call rate, reasoning character stats):

```bash
uv run python -m excelmanus.bench --all                         # All
uv run python -m excelmanus.bench --suite bench/cases/xxx.json  # Specific suite
uv run python -m excelmanus.bench --message "Read first 10 rows"  # Single test
```

## 📖 Configuration Reference

Quick start needs only 3 environment variables. Common configuration categories:

| Category | Key Config |
| --- | --- |
| **Basic** | `EXCELMANUS_API_KEY` / `BASE_URL` / `MODEL` |
| **Auxiliary Model** | `EXCELMANUS_AUX_API_KEY` / `AUX_BASE_URL` / `AUX_MODEL` |
| **VLM (Vision)** | `EXCELMANUS_VLM_MODEL` / `VLM_EXTRACTION_TIER` |
| **Multi-User** | `EXCELMANUS_AUTH_ENABLED` / `JWT_SECRET` |
| **Security** | `EXCELMANUS_DOCKER_SANDBOX` / `GUARD_MODE` |
| **Performance** | `EXCELMANUS_WINDOW_PERCEPTION_*` / `IMAGE_KEEP_ROUNDS` |
| **Playbook** | `EXCELMANUS_PLAYBOOK_ENABLED` |
| **ClawHub** | `EXCELMANUS_CLAWHUB_ENABLED` / `CLAWHUB_REGISTRY_URL` |

Full configuration reference in [Configuration](docs/configuration_en.md).

## ⚡ Performance Highlights

| Optimization | Impact |
| --- | --- |
| **SACR Sparse Compression** | Strips null keys from tool results, up to **74% token savings** on sparse data |
| **Single-Pass Extraction** | Strong VLM models (Gemini 2.5 Pro etc.) complete all 4 extraction phases in one call |
| **Image Lifecycle Management** | Auto-manages image retention/downgrade across turns, avoids redundant transmission |
| **Auxiliary Model Separation** | Routing/sub-agents use lightweight AUX model, main model focuses on reasoning |
| **SSE Event Deduplication** | Unified frontend `dispatchSSEEvent` handler eliminates 3 copies of duplicated code |
| **Database WAL Mode** | Chat history SQLite with WAL, concurrent reads/writes non-blocking |

## 🖥️ Platform Support

| Platform | Status | Notes |
| --- | --- | --- |
| **macOS** | ✅ Full support | Primary dev platform |
| **Linux** | ✅ Full support | Ubuntu / Debian / CentOS / Fedora / Arch etc. |
| **Windows** | ✅ Full support | PowerShell 5.1+ or CMD, requires Python + Node.js |

Start scripts auto-detect OS and package manager, providing precise install commands when dependencies are missing.

## 🛠️ Development

```bash
uv sync --all-extras --dev    # Full install + test dependencies
uv run pytest                 # Run all tests (3900+ cases)

# Run targeted tests only (faster)
uv run pytest tests/test_engine.py tests/test_api.py
```

## 📄 License

[Apache License 2.0](LICENSE) © kilolonion
