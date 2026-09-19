# ExcelManus Operations Manual

## 1. Architecture Overview

```
                           ┌─────────────────────────────────────┐
                           │         User Browser                 │
                           └──────────────┬──────────────────────┘
                                          │ https://<YOUR_DOMAIN>
                                          ▼
                    ┌─────────────────────────────────────────────┐
                    │    Frontend Server <FRONTEND_IP> (China · Alibaba Cloud) │
                    │                                             │
                    │  Nginx (SSL Termination)                    │
                    │    ├─ /api/*  ──▶  <BACKEND_IP>:8000      │
                    │    └─ /*      ──▶  127.0.0.1:3000           │
                    │                                             │
                    │  PM2 Processes                               │
                    │    └─ excelmanus-web  (Next.js, port 3000)  │
                    └──────────────┬──────────────────────────────┘
                                   │ proxy /api/*
                                   ▼
                    ┌─────────────────────────────────────────────┐
                    │    Backend Server <BACKEND_IP> (Overseas · Alibaba Cloud) │
                    │                                             │
                    │  Nginx (Backup SSL, Let's Encrypt)          │
                    │    ├─ /api/*  ──▶  127.0.0.1:8000           │
                    │    └─ /*      ──▶  <FRONTEND_IP>:3000        │
                    │                                             │
                    │  PM2 Processes                               │
                    │    └─ excelmanus-api (Python/uvicorn, 8000) │
                    └─────────────────────────────────────────────┘
```

**Why this split?**

- The frontend server is in China, providing fast access for users, handling DNS entry + SSL termination + static assets
- The backend server is overseas, enabling direct calls to OpenAI/Claude APIs without a proxy
- The backend server also has Nginx + SSL (Let's Encrypt) configured, so it can run independently if DNS is switched over

---

## 2. Server Inventory

| Role | IP | OS | Key Path |
|------|----|----|----------|
| Frontend | `<FRONTEND_IP>` | Alibaba Cloud Linux | `/opt/excelmanus/web` |
| Backend | `<BACKEND_IP>` | Alibaba Cloud Linux | `/opt/excelmanus` |

**SSH Login** (both servers share the same key):

```bash
ssh -i <SSH_KEY_FILE> root@<FRONTEND_IP>   # Frontend
ssh -i <SSH_KEY_FILE> root@<BACKEND_IP>  # Backend
```

---

## 3. Runtime Environment

### 3.1 Frontend Server (<FRONTEND_IP>)

| Component | Version | Path |
|-----------|---------|------|
| Node.js | v22.22.0 | `/www/server/nodejs/v22.22.0/bin` |
| PM2 | 6.x | Same as above |
| Nginx | System built-in | Config: `/etc/nginx/conf.d/excelmanus.conf` |
| SSL Certificate | Self-managed (certbot / ACME / manual upload) | `/etc/ssl/certs/<YOUR_DOMAIN>/` |

**Note**: PM2 on this server requires manually adding to PATH:

```bash
export PATH=/www/server/nodejs/v22.22.0/bin:$PATH
```

### 3.2 Backend Server (<BACKEND_IP>)

| Component | Version | Path |
|-----------|---------|------|
| Python | 3.11.9 | `/usr/local/bin/python3.11` (compiled from source) |
| Node.js | v22.22.0 | `/usr/bin/node` (nodesource RPM) |
| PM2 | 6.0.14 | `/usr/bin/pm2` |
| Nginx | 1.20.1 | Config: `/etc/nginx/conf.d/excelmanus.conf` |
| SSL Certificate | Let's Encrypt (certbot) | `/etc/letsencrypt/live/<YOUR_DOMAIN>/` |
| venv | Python 3.11 | `/opt/excelmanus/venv` |

---

## 4. Firewall Ports

### Frontend Server

```
20/tcp 21/tcp 22/tcp 80/tcp 443/tcp 3000/tcp 8888/tcp 39000-40000/tcp
```

- `3000/tcp` must be open for the backend server's Nginx to reach the frontend origin

### Backend Server

```
20/tcp 21/tcp 22/tcp 80/tcp 443/tcp 8000/tcp 8888/tcp 15996/tcp 39000-40000/tcp
```

- `8000/tcp` must be open for the frontend server's Nginx to forward API requests

**Management commands**:

```bash
firewall-cmd --list-ports                         # List
firewall-cmd --permanent --add-port=PORT/tcp      # Add
firewall-cmd --permanent --remove-port=PORT/tcp   # Remove
firewall-cmd --reload                             # Apply
```

---

## 5. Daily Operations

### 5.1 Local One-Click Start

`deploy/start.sh` launches backend + frontend together, suitable for local development and single-server deployment:

```bash
# macOS / Linux
./deploy/start.sh                          # Dev mode
./deploy/start.sh --prod                   # Production mode (npm run start)
./deploy/start.sh --backend-port 9000      # Custom backend port
./deploy/start.sh --frontend-port 8080     # Custom frontend port
./deploy/start.sh --prod                   # Production (default 1 worker; >1 rebuilds session envelopes across processes and silently busts prompt cache)
./deploy/start.sh --backend-only           # Backend only
./deploy/start.sh --frontend-only          # Frontend only
./deploy/start.sh --log-dir ./logs         # Log output to files
./deploy/start.sh --no-open                # Don't auto-open browser
./deploy/start.sh --skip-deps              # Skip dependency checks
./deploy/start.sh --help                   # Full parameter list
```

**Windows users:**

```powershell
# PowerShell
.\deploy\start.ps1
.\deploy\start.ps1 -Production
.\deploy\start.ps1 -BackendPort 9000 -Production

# CMD
deploy\start.bat
deploy\start.bat --prod
deploy\start.bat --backend-port 9000
```

> Scripts auto-detect OS (macOS / Linux / Windows) and on Linux identify apt / dnf / yum / pacman / zypper / apk package managers, providing install commands when dependencies are missing. Supports graceful shutdown (SIGTERM first, SIGKILL after 5s) and auto-opening the browser. Model setup is in Web Settings and stored in the main database.

### 5.2 Remote deploy (ops-machine `deploy.sh`)

`deploy/deploy.sh` supports separate frontend/backend deployment:

```bash
# Full deployment (backend + frontend)
./deploy/deploy.sh

# Update backend only
./deploy/deploy.sh --backend-only

# Update frontend only
./deploy/deploy.sh --frontend-only

# Build and package frontend artifact locally (recommended)
cd /path/to/excelmanus/web
npm run build
mkdir -p ../web-dist
tar -czf ../web-dist/frontend-standalone.tar.gz .next/standalone .next/static public

# Skip frontend build, restart only
./deploy/deploy.sh --frontend-only --skip-build

# Use locally/CI-built frontend artifact (recommended for low-memory servers)
./deploy/deploy.sh --frontend-only --frontend-artifact ./web-dist/frontend-standalone.tar.gz

# Remote cold build (troubleshooting only, high risk)
./deploy/deploy.sh --frontend-only --cold-build

# Sync from local via rsync (bypasses GitHub)
./deploy/deploy.sh --from-local
```

### 5.3 Manual Operations

**Backend (<BACKEND_IP>)**:

```bash
# Check status
pm2 list

# Restart backend
pm2 restart excelmanus-api

# View logs
pm2 logs excelmanus-api --lines 50 --nostream

# Live logs
pm2 logs excelmanus-api

# Manually update code (Gitee preferred, GitHub fallback)
cd /opt/excelmanus
git fetch https://gitee.com/kilolonion/excelmanus main
# or: git fetch https://github.com/kilolonion/excelmanus main
git reset --hard FETCH_HEAD
# Prefer uv (if installed)
uv sync --all-extras -q 2>/dev/null || { source venv/bin/activate && pip install -e '.[all]' -q; }
pm2 restart excelmanus-api
```

**Frontend (<FRONTEND_IP>)**:

```bash
export PATH=/www/server/nodejs/v22.22.0/bin:$PATH

# Check status
pm2 list

# Restart frontend (without rebuilding)
pm2 restart excelmanus-web

# Rebuild and restart
cd /opt/excelmanus/web
npm install --production=false
NEXT_PUBLIC_BACKEND_ORIGIN= BACKEND_INTERNAL_URL=http://<BACKEND_IP>:8000 npm run build
pm2 restart excelmanus-web

# If default build fails, try webpack fallback
npm run build:webpack
pm2 restart excelmanus-web

# View logs
pm2 logs excelmanus-web --lines 50 --nostream
```

> For low-memory machines (1~2G), prefer using `--frontend-artifact` for artifact-based releases to avoid OOM from on-site cold compilation.
> For cross-region transfers, use rsync with resume support (the script has built-in `--partial --append-verify`).

### 5.4 Health Checks

```bash
# Via domain (full chain)
curl https://<YOUR_DOMAIN>/api/v1/health

# Direct to backend
curl http://<BACKEND_IP>:8000/api/v1/health

# Check frontend reachability
curl -o /dev/null -w "%{http_code}" https://<YOUR_DOMAIN>/
```

---

## 6. Nginx Configuration

### 6.1 Frontend Server `/etc/nginx/conf.d/excelmanus.conf`

```nginx
# HTTP -> HTTPS redirect
server {
    listen 80;
    server_name <YOUR_DOMAIN> www.<YOUR_DOMAIN>;
    return 301 https://$host$request_uri;
}

# HTTPS
server {
    listen 443 ssl http2;
    server_name <YOUR_DOMAIN> www.<YOUR_DOMAIN>;

    ssl_certificate     /etc/ssl/certs/<YOUR_DOMAIN>/fullchain.pem;
    ssl_certificate_key /etc/ssl/certs/<YOUR_DOMAIN>/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    client_max_body_size 100m;

    # SSE streaming endpoint (do NOT use Connection: upgrade, or SSE will fail)
    location /api/v1/chat/stream {
        proxy_pass http://<BACKEND_IP>:8000;
        proxy_http_version 1.1;
        proxy_set_header Connection '';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 600s;
        chunked_transfer_encoding on;
    }

    # Other API requests forwarded to backend server
    location /api/ {
        proxy_pass http://<BACKEND_IP>:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
    }

    # Local Next.js frontend
    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }
}
```

### 6.2 Backend Server `/etc/nginx/conf.d/excelmanus.conf`

This configuration is automatically managed by certbot. When DNS points to this server, it can handle all traffic independently:

- `/api/*` → Local backend (`127.0.0.1:8000`)
- `/*` → Origin to frontend server (`<FRONTEND_IP>:3000`)

**Nginx management commands**:

```bash
nginx -t             # Check config syntax
nginx -s reload      # Graceful reload
systemctl restart nginx  # Full restart
```

---

## 7. Configuration

User settings and model profiles live only in `config_kv` / `model_profiles` (`$EXCELMANUS_HOME/excelmanus.db`), via Web Settings or `/config`.

`deploy.sh` writes `EXCELMANUS_DEPLOY_MODE=server` into the systemd / PM2 process (a locator). Frontend `web/.env.local` is Next.js-only (`NEXT_PUBLIC_BACKEND_ORIGIN`).

Key settings (Settings UI / database keys):

| Setting key | Purpose | Notes |
|----------|---------|-------|
| Model profile (Settings) | API Key / Base URL / model name | Required; stored in `model_profiles` |
| `EXCELMANUS_PROTOCOL` | Model protocol type | `auto` |
| `EXCELMANUS_DEPLOY_MODE` | Deployment mode (`auto`/`standalone`/`server`) | `deploy.sh` writes `server` on systemd/PM2 |
| `EXCELMANUS_MAIN_MODEL_VISION` | Whether the active model accepts image attachments (`auto`/`true`/`false`) | `auto` |
| `EXCELMANUS_SECRET_KEY` | Fernet encryption key seed | Usually empty; auto-generates `.secret_key` |
| `EXCELMANUS_CORS_ALLOW_ORIGINS` | CORS allowlist | Public deploys must include the frontend origin |

Production is `EXCELMANUS_DEPLOY_MODE=server` (set by `deploy.sh`). The API then rejects `/version/upgrade` and `/deploy/execute`; upgrade and rollback run on an ops machine via `./deploy/deploy.sh` (`rollback-to --commit` is checkout + restart). Local Git installs use the Settings stop-then-upgrade flow. See [Upgrade & deploy](hot-update-design.md).

Local standalone upgrades never silently `git reset --hard` (conflicts must be resolved by hand). Server `./deploy/deploy.sh rollback` does run `git reset --hard`.

---

## 8. SSL Certificate Renewal

### Frontend Server

SSL certificate must be managed manually. Recommend using certbot or an ACME client for automatic renewal.

### Backend Server

SSL certificate is managed by Let's Encrypt (certbot) with automatic renewal configured:

```bash
# View certificate info
certbot certificates

# Manual renewal test
certbot renew --dry-run

# Force renewal
certbot renew
```

Use the live output of `certbot certificates` for the expiry date. Do not reuse a historical date from this manual.

---

## 9. DNS Switching Guide

Currently DNS points to the frontend server (`<FRONTEND_IP>`). To switch to the backend server for standalone operation:

1. Change the A records for `<YOUR_DOMAIN>` and `www.<YOUR_DOMAIN>` to `<BACKEND_IP>`
2. The backend server's Nginx already has SSL + bidirectional proxy configured; no additional steps needed
3. Verify after switching: `curl https://<YOUR_DOMAIN>/api/v1/health`

To switch back, change the A records back to `<FRONTEND_IP>`.

---

## 10. Troubleshooting

### Frontend 502

```bash
# 1. Check frontend process
ssh -i <SSH_KEY_FILE> root@<FRONTEND_IP>
export PATH=/www/server/nodejs/v22.22.0/bin:$PATH
pm2 list    # excelmanus-web should be online

# 2. Check backend reachability (from frontend server)
curl http://<BACKEND_IP>:8000/api/v1/health

# 3. If backend is unreachable, check backend
ssh -i <SSH_KEY_FILE> root@<BACKEND_IP>
pm2 list    # excelmanus-api should be online
pm2 logs excelmanus-api --lines 30 --nostream
```

### Backend 500

```bash
ssh -i <SSH_KEY_FILE> root@<BACKEND_IP>
pm2 logs excelmanus-api --lines 50 --nostream
# Model and runtime settings are in Web Settings / the main database
```

### Nginx Configuration Error

```bash
nginx -t    # Syntax check
# View error log
tail -50 /var/log/nginx/error.log
```

### Out of Memory

```bash
free -h
pm2 list    # Check process memory
# Backend API typically uses ~200MB
```

---

## 11. Setting Up a Backend Server from Scratch

If you need to rebuild the backend environment on a new server, follow these steps.

> **Note**: The example below uses Python 3.11, but any version `>=3.10` will work (deploy scripts do not pin a Python micro version).

```bash
# 1. Install build dependencies
yum groupinstall -y "Development Tools"
yum install -y openssl-devel bzip2-devel libffi-devel zlib-devel readline-devel sqlite-devel

# 2. Compile and install Python 3.11
cd /tmp
curl -O https://www.python.org/ftp/python/3.11.9/Python-3.11.9.tgz
tar xzf Python-3.11.9.tgz && cd Python-3.11.9
./configure --enable-optimizations
make -j$(nproc)       # Approximately 10-20 minutes
make altinstall
ln -sf /usr/local/bin/python3.11 /usr/local/bin/python3
ln -sf /usr/local/bin/pip3.11 /usr/local/bin/pip3

# 3. Install Node.js 22
curl -fsSL https://rpm.nodesource.com/setup_22.x | bash -
yum install -y nodejs
npm install -g pm2

# 4. Clone the repository
mkdir -p /opt
# Gitee (faster for China users)
git clone https://gitee.com/kilolonion/excelmanus.git /opt/excelmanus
# or: git clone https://github.com/kilolonion/excelmanus.git /opt/excelmanus
cd /opt/excelmanus

# 5. Install dependencies (uv recommended, pip also works)
# Option A: uv (recommended, auto-creates venv)
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync --all-extras
uv pip install 'httpx[socks]'
# Option B: traditional pip
# python3.11 -m venv venv
# source venv/bin/activate
# pip install -e '.[all]'
# pip install 'httpx[socks]'

# 6. Open Web Settings and add a model profile (main database)
# 7. Configure mcp.json

# 8. Start the backend
pm2 start "venv/bin/python -c \"import uvicorn; uvicorn.run('excelmanus.api:app', host='0.0.0.0', port=8000, log_level='info')\"" --name excelmanus-api --cwd /opt/excelmanus
pm2 save
pm2 startup

# 9. Install Nginx + SSL
yum install -y nginx certbot python3-certbot-nginx
# Write Nginx config (see Section 6)
systemctl start nginx && systemctl enable nginx
certbot --nginx -d <YOUR_DOMAIN> -d www.<YOUR_DOMAIN> --non-interactive --agree-tos --email YOUR_EMAIL

# 10. Open firewall ports
firewall-cmd --permanent --add-port=8000/tcp
firewall-cmd --permanent --add-port=80/tcp
firewall-cmd --permanent --add-port=443/tcp
firewall-cmd --reload
```

---

## 12. File Inventory

```
Project Root/
├── deploy/
│   ├── start.sh           # One-click start script (macOS / Linux)
│   ├── start.ps1          # One-click start script (Windows PowerShell)
│   ├── start.bat          # One-click start script (Windows CMD)
│   ├── deploy.sh          # Remote deployment script (macOS / Linux)
│   ├── deploy.ps1         # Remote deployment script (Windows PowerShell)
│   ├── nginx.conf         # Nginx reverse proxy config (127.0.0.1 sample; see this manual for production)
│   └── certs/             # TLS certificates
├── excelmanus/
│   ├── config.py           # Runtime config (database settings + defaults)
│   ├── context_budget.py   # Context budget manager
│   ├── model_probe.py      # Model metadata probing (context window auto-correction)
│   └── security/
│       └── cipher.py       # Fernet symmetric encryption (API Key / Token encrypted storage)
├── mcp.json               # MCP server configuration
└── docs/
    └── ops-manual.md      # This manual
```
