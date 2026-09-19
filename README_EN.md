<p align="center">
  <img src="web/public/logo.svg" width="380" alt="ExcelManus" />
</p>

<h3 align="center">Open-Source AI Agent Framework for Excel, Driven by Natural Language</h3>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue.svg" alt="License" /></a>
  <a href="https://github.com/kilolonion/excelmanus"><img src="https://img.shields.io/github/stars/kilolonion/excelmanus?style=social" alt="GitHub Stars" /></a>
  <img src="https://img.shields.io/badge/python-≥3.10-3776AB.svg?logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/version-1.7.3-green.svg" alt="Version" />
  <img src="https://img.shields.io/badge/Next.js-16-black?logo=next.js" alt="Next.js" />
  <img src="https://img.shields.io/badge/pytest-included-brightgreen.svg" alt="Tests" />
</p>

<p align="center">
  <a href="README.md">中文</a> · English · <a href="docs/configuration_en.md">Configuration</a> · <a href="docs/ops-manual_en.md">Ops Manual</a>
</p>

<p align="center">
  <img src="docs/images/webui-desktop.png" width="720" alt="Web UI" />
</p>

---

**ExcelManus** is a fully open-source, LLM-powered Excel Agent framework. Describe what you need in plain language and it will read data, write formulas, run analysis scripts, and create charts — like an AI assistant that truly understands Excel.

- **Two interfaces** — Web UI / REST API
- **Any LLM** — OpenAI · Claude · Gemini · DeepSeek · Qwen · Kimi · xAI · Doubao · local Ollama / vLLM, plug and play
- **Production-ready** — Local Git stop-then-upgrade · server deploy.sh · single-user workspace · approval flows · version rollback

> 💡 First launch: open Web Settings and add a model profile (stored in `model_profiles`). Web / API share the same profiles.

---

## ✨ Key Capabilities

<table>
<tr>
<td width="50%">

### 📊 Excel and Word
Cell read/write · Formulas · VLOOKUP · Batch fill · Multi-sheet
`.xls` / `.xlsb` convert transparently to `.xlsx`; `.xlsx` / `.xlsm` / `.csv` / `.tsv` are native
Word `.docx` read, edit, and generate (first-class, same as Excel)

### 📈 Data Analysis & Visualization
Filter, sort, aggregate, pivot tables; complex logic auto-generates Python scripts
Bar · line · pie charts embedded in Excel or exported as HD images

### 🖼️ Vision Recognition & Extraction
Table screenshots are attached for the main model, which produces structured Excel data
No separate vision pipeline and no satellite VLM description step

### 🔄 Version Management & Diff
Writes land on the user path; history lives in `.excelmanus/revisions/`; `/undo` rolls back
Excel write diff visualization, text file unified diff display

### ✅ Task Evidence & Agent Review
Record optional check targets; the primary agent chooses relevant readback, comparison, and formula checks
No hidden acceptance agent; permissions, file safety, backups, and rollback remain independent

</td>
<td width="50%">

### 🧠 Persistent Memory & Session History Awareness
Cross-session memory for user preferences and operation patterns; the model reads it via memory tools by default — it is not auto-injected at session start.
**Session summary (optional)**: When enabled, a structured summary can be written at session end (`session_summary_enabled` is off by default). Filename/recency retrieval and injection into new sessions are not wired.

### 🧩 Skillpack
One directory + `SKILL.md` is one skill, auto-discovery; the model loads it with the `skill` tool
Import skill packs from a local file or GitHub

### 🔌 MCP & Subagent
Connect external MCP Servers to extend toolset
Delegation is the model calling `delegate`; `/subagent` toggles the feature. Large files or complex tasks are not auto-delegated.

### 🔄 Local stop-then-upgrade
Settings one-click update: stop processes → backup `$EXCELMANUS_HOME` → git fast-forward → start again
Server deploys run `deploy.sh` on an ops machine, not from the production API

</td>
</tr>
</table>

## 🚀 Quick Start

> **Prerequisites**: Python ≥ 3.10 · Node.js ≥ 20.9 (for Web UI; Next.js 16)

### Option 1: One-Click Start (Recommended)

Auto-installs dependencies and launches both backend and frontend.

<details open>
<summary><b>🍎 macOS / 🐧 Linux — Start Script</b></summary>

```bash
git clone https://github.com/kilolonion/excelmanus.git
cd excelmanus
chmod +x ./deploy/start.sh
./deploy/start.sh
```

On first launch, the script interactively prompts for LLM config (API Key, Base URL, Model). Browser auto-opens `http://localhost:3000`.

```bash
./deploy/start.sh --prod              # Production mode (default 1 worker)
./deploy/start.sh --backend-port 9000 # Custom port
./deploy/start.sh --workers 1         # Recommended: session state is in-process; >1 workers silently bust prompt cache
./deploy/start.sh --help              # All options
```

</details>

<details>
<summary><b>🪟 Windows — start.ps1 / start.bat</b></summary>

```powershell
git clone https://github.com/kilolonion/excelmanus.git
cd excelmanus
.\deploy\start.ps1
```

```bat
deploy\start.bat
```

On first launch, the script interactively prompts for LLM config. Browser opens `http://localhost:3000`.

```powershell
.\deploy\start.ps1 -Production
.\deploy\start.ps1 -BackendPort 9000
deploy\start.bat --prod
```

</details>

### Option 2: Manual Install (uv)

For users who want precise control over dependencies. [uv](https://docs.astral.sh/uv/) is 10–100x faster than pip.

```bash
# 1. Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh
# Windows: powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# 2. Clone and install
git clone https://github.com/kilolonion/excelmanus.git
cd excelmanus
uv sync --all-extras     # Full install: web/analysis (also supports pip install ".[all]")

# 3. Configure
# After launch, open Web Settings and add a model profile (stored in the main database).

# 4. Launch
uv run excelmanus-api    # Web API (http://localhost:8000)
cd web && npm i && npm run dev   # Web frontend (http://localhost:3000)
```

### Start Chatting

Type natural language in the Web UI:

```
> Read the first 10 rows of sales.xlsx
> Sum column A amounts and write to B1
> Group sales by region and generate a bar chart
> Recreate this table screenshot as an Excel file
```

## 💻 Two Interfaces

### Web UI

Built on **Next.js + Univer.js**, providing a full visual experience.

| Feature | Description |
| --- | --- |
| **SSE Streaming** | Real-time display of thinking, tool calls, sub-agent execution; auto-reconnect |
| **Excel Side Panel** | Embedded Univer viewer, live preview/edit, range selection, full-screen mode |
| **Excel & Text Diff** | Before/after comparison on every write |
| **Multi-Session** | SQLite + IndexedDB 3-tier cache, survives refresh/restart |
| **File Interaction** | Drag & drop upload, `@` reference files and skills; `.xls` / `.xlsb` auto-converted |
| **Approval Flow** | Confirmation dialog for high-risk operations, changes auto-snapshot |
| **Optimistic UI** | Messages appear immediately, writes optimistic update + rollback on failure |
| **Error Guidance** | Actionable suggestion cards (retry / check settings / copy diagnostic ID) |
| **API Pool** | Optional credential pool and subscription rotation (off by default) |
| **Plan Mode** | `/plan` toggle; execute after confirming the plan — complex tasks are not auto-decomposed |
| **Upgrade notification** | Detects new versions; local stop-then-upgrade then probe |

<p align="center">
  <img src="docs/images/webui-mobile.png" width="300" alt="Mobile" />
</p>
<p align="center"><sub>Responsive layout — works on mobile</sub></p>

### REST API

Available once `excelmanus-api` starts. SSE pushes 30+ event types.

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
| `GET /api/v1/files/excel` | Excel file stream |
| `GET /api/v1/files/excel/snapshot` | Excel JSON snapshot |
| `POST /api/v1/files/excel/write` | Side panel write-back |
| `GET /api/v1/files/word` | Word file stream |
| `GET /api/v1/files/word/snapshot` | Word JSON snapshot |
| `POST /api/v1/files/word/write` | Word write-back |
| `GET /api/v1/workspaces` | Registered workspaces |
| `POST /api/v1/workspaces` | Register a local folder |
| `GET /api/v1/revisions` | File revision history |
| `POST /api/v1/revisions/restore` | Restore a historical revision |
| `GET /api/v1/skills` | Skill list |
| `GET /api/v1/version/check` | Version check |
| `POST /api/v1/version/upgrade` | Local stop-then-upgrade (standalone + loopback only) |
| `GET /api/v1/auth/providers/openai-codex/status` | Codex connection status |
| `POST /api/v1/config/export` | Export config |
| `GET /api/v1/health` | Health check |

</details>

## 🤖 Model Support

ExcelManus auto-detects model providers by URL — zero-config switching:

| Provider | Description |
| --- | --- |
| **OpenAI Compatible** | Default protocol. Any OpenAI-compatible API — Ollama / vLLM / LM Studio / DeepSeek etc. |
| **Claude (Anthropic)** | Auto-switches when URL contains `anthropic`; Claude 5 uses adaptive thinking |
| **Gemini (Google)** | Auto-switches when URL contains `googleapis` / `generativelanguage` |
| **OpenAI Responses API** | Next-gen inference API, enable with `EXCELMANUS_USE_RESPONSES_API=1` |
| **OpenAI Codex** | ChatGPT subscription OAuth (browser PKCE or device code) binds Codex; private models auto-discovered, no manual Key |
| **MiniMax / Zhipu / Qwen / Kimi / Doubao / xAI** | Auto-detects base_url; falls back to a curated model list when `/models` is unavailable |

### Model Profiles

Add multiple model profiles in Settings and activate one for chat, subagents, and compaction. `/model <name>` switches the active profile.

### Model Capability Probing

On first use of a new model, ExcelManus auto-probes its capability boundaries (vision, function calling, context window, etc.) and dynamically adjusts tool strategies — no manual configuration needed.

## 🔒 Security

| Mechanism | Description |
| --- | --- |
| **Path Sandbox** | Reads/writes restricted to working directory, path traversal and symlink escapes rejected |
| **Code Review** | `run_code` static analysis, Green / Yellow / Red tier auto-approval |
| **Local process fence** | `run_code` runs in a local subprocess (path jail, restricted builtins, timeout); no Docker |
| **Operation Approval** | High-risk writes require confirmation, changes auto-record diffs and snapshots |
| **Version Chain** | Writes land on the user path; history lives in `.excelmanus/revisions/`; `/undo` rolls back |
| **MCP Whitelist** | External tools require per-item confirmation by default |
| **Workspace Boundary** | Credentials and memory are process-wide; multiple local folders can be registered as workspaces. Multiple chats are not multi-tenancy |

## 🧩 Skillpack

One directory + one `SKILL.md` (with `name` and `description`) to create a skill. Auto-discovery; activation uses the `skill` tool (or slash `/<name>` / `@skill`). Supports Hooks, command dispatch, and MCP dependency declarations. Import from a local path or GitHub URL.

<details>
<summary>📦 Built-in Skills</summary>

| Skill | Purpose |
| --- | --- |
| `data_basic` | Read, analyze, filter, transform |
| `chart_basic` | Charts (embedded + image export) |
| `format_basic` | Styles, conditional formatting, advanced layout |
| `file_ops` | File management |
| `sheet_ops` | Worksheet & cross-sheet operations |
| `excel_code_runner` | Python scripts for large files |
| `run_code_templates` | Common code templates |
| `word_basic` | Word read, edit, and content generation |
| `word_code_runner` | Complex Word work via python-docx scripts |

</details>

Protocol details in [`docs/skillpack_protocol_en.md`](docs/skillpack_protocol_en.md).

## Single-user architecture

Credentials and memory are process-wide; multiple local folders can be registered as workspaces, and each conversation binds one folder. Multiple conversations remain.
Configure Codex subscription OAuth under Settings → Models → Subscription & OAuth; it is not tied to a login account.

Legacy `users/{id}/` trees are not auto-merged. Copy the one directory you want into `data_root` / `workspace_root`; per-user `data.db` files are not imported. See [Configuration](docs/configuration_en.md).

**OpenAI Codex subscription**: bind a ChatGPT/Codex subscription via browser PKCE or device code; private models are auto-discovered, no manual API Key.

> **Split frontend/backend deploys**: the OAuth callback is a frontend page that exchanges the token with the backend from the browser. Set the redirect URI to `https://your-domain/auth/codex/callback`.

## 🏗️ Deployment

### Local start (recommended)

```bash
./deploy/start.sh              # macOS / Linux dev mode
./deploy/start.sh --prod       # Production mode
.\deploy\start.ps1 -Production # Windows PowerShell
deploy\start.bat --prod        # Windows CMD
```

Visit `http://localhost:3000`. Supports `--backend-port` · `--frontend-port` · `--workers` · `--backend-only` and more.

Local upgrade: Settings → Apply update, or stop the service then `./deploy/update.sh`. The helper stops the process group, backs up `$EXCELMANUS_HOME`, fast-forwards git, then starts again.

**Former Compose / image users**: copy the volume's database and uploads into `$EXCELMANUS_HOME` (default `~/.excelmanus`), then use `./deploy/start.sh` or PM2 / systemd on the server. Docker is no longer a product install path.

### Remote Deploy

Run deploy scripts on an **ops machine** over SSH. Topologies: single-server, split frontend/backend, or local. A production process (`EXCELMANUS_DEPLOY_MODE=server`) cannot upgrade itself or trigger remote deploy.

```bash
./deploy/deploy.sh                    # Full deploy
./deploy/deploy.sh --backend-only     # Backend only
./deploy/deploy.sh --frontend-only    # Frontend only
./deploy/deploy.sh rollback           # Rollback to previous version
./deploy/deploy.sh rollback-to --commit <hash>
./deploy/deploy.sh check              # Environment + connectivity check
```

<details>
<summary>🔐 Deployment Safety</summary>

Three-layer protection to prevent 502 during deployment:

| Layer | Mechanism | Description |
| --- | --- | --- |
| **Build exit code** | No pipe swallowing exit codes | Build failure aborts immediately |
| **Artifact validation** | BUILD_ID + routes-manifest.json | Incomplete artifacts refuse restart |
| **Startup fallback** | standalone vs next start auto-detect | Compatible with all Next.js outputs |

On failure, keeps current running version. Auto-excludes `.env`, `data/`, `workspace/`.

</details>

### Upgrade

Local (standalone) stop-then-upgrade: Settings one-click update, or stop the service then `./deploy/update.sh`. The helper kills the start process group, backs up `$EXCELMANUS_HOME`, fast-forwards git, then starts again. Conflicts are not `reset --hard`.

On a server (`EXCELMANUS_DEPLOY_MODE=server`), run `./deploy/deploy.sh` from an ops machine; the production API refuses to upgrade itself. Rollback: `./deploy/deploy.sh rollback-to --commit <hash>`.

See [Upgrade & deploy](docs/hot-update-design.md).

For manual deployment, see [Ops Manual](docs/ops-manual_en.md).

## ⚡ Performance Highlights

| Optimization | Impact |
| --- | --- |
| **Claude Layered Cache** | System prompt split into stable prefix + dynamic block, 2nd request TTFT drops to 3-5s |
| **SACR Sparse Compression** | Strips null keys from tool results; tests show over 50% token savings on sparse data |
| **Image Lifecycle Management** | Auto-manages image retention/downgrade across turns |
| **Single Active Model** | Chat, subagents, and compaction share the active profile |
| **Context Budget Management** | Dynamic budget allocation with uniform truncation of older messages |
| **SSE Event Deduplication** | Unified frontend `dispatchSSEEvent` handler |
| **Database WAL Mode** | SQLite WAL for concurrent reads/writes |

## 📖 Configuration Reference

Model and runtime options are saved from **Web Settings** into the main database. Common categories:

| Category | Key Config |
| --- | --- |
| **Basic** | `EXCELMANUS_API_KEY` / `EXCELMANUS_BASE_URL` / `EXCELMANUS_MODEL` |
| **Vision** | `EXCELMANUS_MAIN_MODEL_VISION` / `EXCELMANUS_IMAGE_PIXEL_BUDGET` |
| **Security** | `EXCELMANUS_CODE_POLICY_*` / `EXCELMANUS_MANAGE_TOKEN` |
| **Performance** | `EXCELMANUS_IMAGE_PIXEL_BUDGET` |
| **Session Summary** | `EXCELMANUS_SESSION_SUMMARY_ENABLED` / `EXCELMANUS_SESSION_SUMMARY_MIN_TURNS` |

Full reference in [Configuration](docs/configuration_en.md).

## 🖥️ Platform Support

| Platform | Status | Notes |
| --- | --- | --- |
| **macOS** | ✅ Full support | Primary dev platform |
| **Linux** | ✅ Full support | Ubuntu / Debian / CentOS / Fedora / Arch etc. |
| **Windows** | ✅ Full support | PowerShell 5.1+ or CMD |

Start scripts auto-detect OS and package manager, providing precise install commands when dependencies are missing.

## 🧪 Evaluation Framework

Built-in Bench evaluation with multi-turn cases, auto-assertion, JSON logs, and suite-level concurrency:

```bash
uv run python -m excelmanus.bench --all                         # Default short suites
uv run python -m excelmanus.bench --suite bench/cases/xxx.json  # Specific suite
uv run python -m excelmanus.bench --message "Read first 10 rows"  # Single test
```

The long experiential suite (no golden answers) lives in `bench/` and is not included in `--all`. See `bench/README.md`.

## 🛠️ Development & Contributing

```bash
uv sync --all-extras --dev    # Full install (web/analysis) + test dependencies
uv run pytest tests/test_engine.py tests/test_api.py  # Targeted tests
```

PRs and Issues welcome! Please include tests with new code and run the tests related to your change.

## ⭐ Star History

If ExcelManus helps you, please give us a Star 🌟

<p align="center">
  <a href="https://github.com/kilolonion/excelmanus/stargazers">
    <img src="https://starchart.cc/kilolonion/excelmanus.svg?variant=adaptive" width="600" alt="Star History" />
  </a>
</p>

## 📄 License

[Apache License 2.0](LICENSE) © kilolonion
