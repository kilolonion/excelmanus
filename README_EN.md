<p align="center">
  <img src="web/public/logo.png" width="380" alt="ExcelManus" />
</p>

<h3 align="center">AI Agent that operates Excel with natural language</h3>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue.svg" alt="License" /></a>
  <img src="https://img.shields.io/badge/python-≥3.10-3776AB.svg?logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/version-1.6.0-green.svg" alt="Version" />
  <img src="https://img.shields.io/badge/Next.js-15-black?logo=next.js" alt="Next.js" />
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
Cells · Formulas · VLOOKUP · Batch fill · Multi-sheet operations

### 📈 Data Analysis & Charts
Filter, sort, aggregate, pivot tables; complex logic auto-generates Python scripts. Bar, line, pie charts embedded in Excel or exported as images.

### 🖼️ Image Recognition
Table screenshot → structured data via 4-stage progressive pipeline extracting data + styles + formulas

### 🔄 Version Management
Staging / Audit / CoW version chain, `/undo` precise rollback to any operation

</td>
<td width="50%">

### 🧠 Persistent Memory
Cross-session memory for preferences and patterns, auto-adapts behavior

### 🧩 Skillpack
One Markdown = one skill. Auto-discovery, on-demand activation, supports Hooks and command dispatch

### 🔌 MCP & Subagent
Connect external MCP Servers to extend toolset; large files and complex tasks auto-delegated to sub-agents

### 👥 Multi-User
Independent workspace / database / session isolation, admin panel for permissions and usage control

</td>
</tr>
</table>

## 🚀 Quick Start

**1. Install**

```bash
pip install ".[cli]"          # CLI mode (lightweight, terminal only)
pip install ".[web]"          # Web API mode (FastAPI / auth etc.)
pip install ".[all]"          # Full install (CLI + Web)
pip install .                  # Core only (for library usage)
```

**2. Configure** — create `.env` with just 3 variables:

```dotenv
EXCELMANUS_API_KEY=your-api-key
EXCELMANUS_BASE_URL=https://your-llm-endpoint/v1
EXCELMANUS_MODEL=your-model-id
```

> Works with any OpenAI-compatible API. URLs containing `anthropic` or `googleapis` auto-switch to native protocol.

**3. Run**

```bash
excelmanus            # CLI mode
excelmanus-api        # Web UI + REST API
```

Or use the one-click start script (launches backend + frontend together):

```bash
./deploy/start.sh                    # macOS / Linux dev mode
./deploy/start.sh --prod             # Production mode
./deploy/start.sh --backend-port 9000  # Custom port
```

Windows users:

```powershell
.\deploy\start.ps1                   # PowerShell
deploy\start.bat                     # CMD
```

**Try it out:**

```
> Read the first 10 rows of sales.xlsx
> Sum column A amounts and write to B1
> Group sales by region and generate a bar chart
```

> After first run, settings migrate to local database. Manage via `/config` command or Web UI settings panel.

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

- **SSE Streaming** — Real-time display of thinking process, tool calls, sub-agent execution
- **Excel Side Panel** — Embedded viewer, live preview/edit, range selection support
- **Write Diff** — Before/after comparison on every modification
- **Multi-Session** — Persistent history, seamless switching
- **File Interaction** — Drag & drop upload, `@` reference files and skills
- **Approval Flow** — Confirmation dialog for high-risk operations

<p align="center">
  <img src="docs/images/webui-mobile.png" width="300" alt="Mobile" />
</p>
<p align="center"><sub>Mobile-friendly — responsive layout</sub></p>

### REST API

Available once `excelmanus-api` starts. SSE pushes 25+ event types.

<details>
<summary>📋 Main Endpoints</summary>

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

</details>

## 🤖 Model Support

| Provider | Description |
| --- | --- |
| **OpenAI Compatible** | Default protocol, works with any compatible API |
| **Claude (Anthropic)** | Auto-switches when URL contains `anthropic`, supports extended thinking |
| **Gemini (Google)** | Auto-switches when URL contains `googleapis` / `generativelanguage` |
| **OpenAI Responses API** | Enable with `EXCELMANUS_USE_RESPONSES_API=1` |

Configure an **auxiliary model (AUX)** for routing, sub-agents, and window management. Main and auxiliary models switch independently.

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

## 🧩 Skillpack

One directory + one `SKILL.md` (with `name` and `description`) to create a skill. Auto-discovery, on-demand activation, supports Hooks, command dispatch, and MCP dependency declarations.

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

### Remote Update

```bash
./deploy/deploy.sh                  # Full deploy
./deploy/deploy.sh --backend-only   # Backend only
./deploy/deploy.sh --frontend-only  # Frontend only
```

> Automatically excludes `.env`, `data/`, `workspace/` — won't overwrite server data. Works on macOS and Linux.

## 👥 Multi-User

```dotenv
EXCELMANUS_AUTH_ENABLED=true
EXCELMANUS_JWT_SECRET=your-random-secret-key-at-least-64-chars
```

Supports **email/password**, **GitHub OAuth**, and **Google OAuth** login. Each user gets an independent workspace and database. First registered user becomes admin.

See [Configuration](docs/configuration_en.md) for details.

## 🧪 Evaluation Framework

Built-in Bench evaluation with multi-turn cases, auto-assertion, JSON logs, and suite-level concurrency:

```bash
python -m excelmanus.bench --all                         # All
python -m excelmanus.bench --suite bench/cases/xxx.json  # Specific suite
python -m excelmanus.bench --message "Read first 10 rows"  # Single test
```

## 📖 Configuration Reference

Quick start needs only 3 environment variables. Full configuration (window perception, security policies, Subagent, MCP, VLM, Embedding, etc.) in [Configuration](docs/configuration_en.md).

## 🖥️ Platform Support

| Platform | Status | Notes |
| --- | --- | --- |
| **macOS** | ✅ Full support | Primary dev platform |
| **Linux** | ✅ Full support | Ubuntu / Debian / CentOS / Fedora / Arch etc. |
| **Windows** | ✅ Full support | PowerShell 5.1+ or CMD, requires Python + Node.js |

Start scripts auto-detect OS and package manager, providing precise install commands when dependencies are missing.

## 🛠️ Development

```bash
pip install -e ".[all,dev]"   # Full install + test dependencies
pytest
```

## 📄 License

[Apache License 2.0](LICENSE) © kilolonion
