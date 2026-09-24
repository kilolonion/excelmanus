# ExcelManus operations guide

Applies to: 1.8.1 source tree · Updated: 2026-09-21

[Documentation](README.md) · [中文](ops-manual.md) · [Configuration](configuration_en.md)

This guide covers source launches, server deployment, backups, and troubleshooting. Paths, domains, and service accounts are examples. See the [Desktop README](../desktop/README.md) for app packaging and desktop data locations.

## 1. Choose an installation

| Installation | Use case | Start and update |
| --- | --- | --- |
| Desktop app | Local use | The app starts bundled services; install a new bundle to update |
| Local Git checkout | Development or ongoing local use | Start with `deploy/start.*`; stop services before updating |
| Server | Remote access through a controlled entry point | Manage processes with PM2 / systemd and deploy from an operations machine |

ExcelManus is a single-user application. Workspaces and conversations share credentials, memory, and external service settings. A management token does not provide tenant isolation. Source installations require Python ≥ 3.10, Node.js ≥ 20.9, and Git; uv is recommended. Servers also need a process manager and reverse proxy.

Keep **one backend worker** on a single host. Running conversations, approvals, and tasks include in-process state; adding workers is not a complete scaling solution.

## 2. Start a local checkout

From the repository root:

```bash
./deploy/start.sh
./deploy/start.sh --prod
./deploy/start.sh --backend-port 9000 --frontend-port 8080
./deploy/start.sh --backend-only
./deploy/start.sh --no-open --log-dir ./logs
./deploy/start.sh --help
```

Windows PowerShell:

```powershell
.\deploy\start.ps1
.\deploy\start.ps1 -Production
.\deploy\start.ps1 -BackendPort 9000 -FrontendPort 8080
```

CMD users can run `deploy\start.bat` or `deploy\start.bat --prod`. Open [http://localhost:3000](http://localhost:3000); the API defaults to `127.0.0.1:8000`. Add and activate a model in Settings after launch.

To start services manually, use two terminals:

```bash
# Terminal 1: repository root
uv sync --frozen --extra web --extra analysis
uv run excelmanus-api --host 127.0.0.1 --port 8000
```

```bash
# Terminal 2: repository root
cd web
npm ci
npm run dev
```

When choosing different ports, update the frontend connection settings as described in the [Web README](../web/README.md).

## 3. Settings and data locations

| Content | Location and management |
| --- | --- |
| Model profiles and product settings | `model_profiles` / `config_kv` in the main database; use Settings or configuration import |
| Main database | `$EXCELMANUS_HOME/excelmanus.db` by default |
| Credential encryption key | `$EXCELMANUS_HOME/.secret_key` by default; retain it with the database |
| Default workspace | `EXCELMANUS_DATA_ROOT`, normally `$EXCELMANUS_HOME/data` when unset |
| Other workspaces | Registered local folders, retained at their original locations |
| File revisions | `.excelmanus/revisions/` inside each workspace |
| Deployment inventory | `deploy/.env.deploy` on the operations machine, copied from the example |
| Frontend connection settings | Next.js process environment or `web/.env.local` |

Source installations default to `~/.excelmanus`. Desktop uses `profile/` under Electron's user data directory. These locations are not merged automatically. Do not run desktop and source services against the same profile concurrently.

Project `.env` and user `config.env` files are no longer product settings sources. The launcher supplies locators such as `EXCELMANUS_HOME`, `EXCELMANUS_DB_PATH`, bind parameters, `EXCELMANUS_DEPLOY_MODE`, and `EXCELMANUS_MANAGE_TOKEN`. See the [configuration reference](configuration_en.md).

## 4. Protect server access

Recommended single-host topology:

```text
Browser ── HTTPS ── Nginx
                   ├─ /api/ ── 127.0.0.1:8000 (FastAPI)
                   └─ /     ── 127.0.0.1:3000 (Next.js)
```

1. Set `EXCELMANUS_DEPLOY_MODE=server` for the backend. Servers disable web-driven self-updates and remote deployment by default; backup/restore endpoints also remain limited to local origins. To let an administrator update from the web UI, see section 7.
2. Configure `EXCELMANUS_MANAGE_TOKEN` with at least 16 characters. A reverse proxy can expose a loopback backend, so loopback binding alone is not remote access protection.
3. Enter the same token in the browser's token prompt. API clients send `Authorization: Bearer <token>`. Do not embed it in public frontend variables or have an unauthenticated proxy inject it for every visitor.
4. On one host, only the proxy needs access to application ports. Across hosts, use a private network, VPN, or explicitly controlled backend endpoint instead of opening ports 3000/8000 to every public source.

Health checks and CORS preflight requests remain accessible without a token. The token does not protect all frontend pages, static assets, or API documentation. Apply access control at the network entry point if the whole site must be private.

Example systemd service: first create a dedicated account, prepare the source checkout and `.venv` at `/srv/excelmanus`, and give the account access to `/var/lib/excelmanus`.

```ini
# /etc/systemd/system/excelmanus-api.service
[Unit]
Description=ExcelManus API
After=network-online.target

[Service]
Type=simple
User=excelmanus
WorkingDirectory=/srv/excelmanus
EnvironmentFile=/etc/excelmanus/runtime.env
ExecStart=/srv/excelmanus/.venv/bin/excelmanus-api --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Example `/etc/excelmanus/runtime.env`, read by the service manager:

```dotenv
EXCELMANUS_HOME=/var/lib/excelmanus
EXCELMANUS_DEPLOY_MODE=server
EXCELMANUS_MANAGE_TOKEN=replace-with-your-own-random-token
```

Replace the token placeholder with your own random value and restrict access to the file. Save model credentials through Web Settings. With PM2, configure the same process parameters and confirm they survive a restart.

## 5. Reverse proxy and frontend origin

For a production Next.js process:

```dotenv
EXCELMANUS_RUNTIME_BACKEND_ORIGIN=same-origin
BACKEND_INTERNAL_URL=http://127.0.0.1:8000
```

The first value is read at runtime and routes browser API calls through the same origin. The second supplies Next.js rewrites and should also be correct at build time. Cross-origin connections additionally require the product setting `EXCELMANUS_CORS_ALLOW_ORIGINS`.

Add this fragment to an Nginx `server` block with your domain and TLS certificate configured:

```nginx
client_max_body_size 100m;

location /api/ {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Authorization $http_authorization;
    proxy_set_header Connection "";
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 600s;
}

location / {
    proxy_pass http://127.0.0.1:3000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Disabling buffering for all `/api/` requests covers initial and reconnected streams. Align upload limits with the application; `100m` is an example proxy limit. Run `nginx -t` before reloading. Certificate renewal and expiry depend on your deployment.

## 6. Deploy from an operations machine

Copy the deployment inventory and fill in hosts, SSH keys, remote paths, and branch:

```bash
cp deploy/.env.deploy.example deploy/.env.deploy
bash ./deploy/deploy.sh --help
bash ./deploy/deploy.sh check --single-server --host server.example.com --venv .venv
bash ./deploy/deploy.sh --single-server --host server.example.com --venv .venv --dry-run
bash ./deploy/deploy.sh --single-server --host server.example.com --venv .venv
```

`uv sync` creates `.venv`, so these examples explicitly pass `--venv .venv`. The script supports `--single-server`, `--split-server`, `--local`, and PM2 / systemd. Windows operators can use `deploy/deploy.ps1`; consult its help for platform-specific parameters.

Before exposing the service, verify the management token, durable paths, and proxy configuration. The script does not generate a token or create tenant accounts. Its backend defaults to loopback; split deployments need an independently configured, controlled connection between hosts.

Common operations:

```bash
bash ./deploy/deploy.sh --backend-only --venv .venv
bash ./deploy/deploy.sh --frontend-only
bash ./deploy/deploy.sh status
bash ./deploy/deploy.sh history
bash ./deploy/deploy.sh logs
bash ./deploy/deploy.sh rollback
bash ./deploy/deploy.sh rollback-to --commit COMMIT_SHA
```

These commands use the deployment inventory, so confirm its topology and targets first. Git synchronization and some rollback paths use `git reset --hard`. Keep uncommitted development work outside the deployment checkout. Inspect the synchronization scope before using `--from-local`.

For servers with limited memory, build a frontend artifact beforehand. From the repository root:

```bash
npm --prefix web ci
npm --prefix web run build
mkdir -p web-dist
tar -czf web-dist/frontend-standalone.tar.gz -C web .next/standalone .next/static public
bash ./deploy/deploy.sh --frontend-only --frontend-artifact ./web-dist/frontend-standalone.tar.gz
```

Build for a compatible operating system, architecture, and Node.js runtime; do not assume a macOS artifact works on Linux. Deployment locks, build checks, and health checks detect some failures, but do not establish business correctness or zero-downtime operation.

## 7. Updates, backups, and recovery

Local source installations can apply a stop-then-update from Settings → Version → Update frontend and backend, or run `./deploy/update.sh` after stopping services. The updater backs up application data and attempts a fast-forward; uncommitted changes, diverged branches, or path conflicts stop the update instead of stashing or overwriting files. Web updates require a single-worker instance launched by `deploy/start.sh` / `start.ps1` with frontend and backend on the same host, refuse to start while tasks are running, and include a downtime window before the page prompts a refresh. Servers keep this entry disabled unless `EXCELMANUS_WEB_UPGRADE_ENABLED=1` is set together with administrator login protection. Multi-worker, split deployments, and packaged installs still update through the operations machine; desktop apps install new bundles. See [update behavior](hot-update-design.md).

The built-in update backup covers the main databases, `config.env`, `.secret_key`, and the `data/`, `memory/`, and `skillpacks/` directories, but it is not a full profile copy; workspaces outside the data directory, external databases, and deployment inventories are excluded. Do not rely solely on that generated backup for migration or disaster recovery.

Stop related processes before a backup so SQLite, execution state, and files form a consistent set. Save at least:

- All of `EXCELMANUS_HOME`, including the database and `.secret_key`. Back up a custom external `EXCELMANUS_DB_PATH` separately.
- Every registered workspace outside the data directory, including its `.excelmanus/revisions/`.
- Custom skills, MCP configuration, and process manager configuration, which may live elsewhere.

After stopping services, an example profile backup to a location outside the profile is:

```bash
tar -czf /secure-backups/excelmanus-profile.tar.gz -C /path/to/profile .
```

Before restoring, back up the current data. Restore matching database, key, and files; check ownership and permissions before restarting. Reverting code does not automatically revert database structure or every workspace file. Deleting a conversation does not remove all revisions, independent logs, or backups.

Select and migrate legacy `users/{id}/` directories or container volumes manually. Do not merge multiple user databases by copying them together. See [configuration](configuration_en.md) for migration of legacy `outputs/backups`.

## 8. Checks and troubleshooting

Check services after deployment, then perform a small real file task:

```bash
curl --fail http://127.0.0.1:8000/api/v1/health
curl --fail https://your-domain.example/api/v1/health
```

Also verify that unauthorized API requests are rejected, the selected model responds, upload/download work, SSE events arrive, and a file edit and revision restore behave as expected. HTTP 200 alone is not release acceptance.

| Symptom | First checks |
| --- | --- |
| Frontend 502 | Next.js/API processes, proxy ports, build artifacts, and service logs |
| API 401 | Browser token, backend environment, and Authorization forwarding |
| Model calls fail | Active profile, protocol, Base URL, model access, and provider rate limits |
| SSE appears stalled | Proxy buffering, read timeout, runtime backend origin, and CORS |
| Wrong backend after a port change | Runtime origin and stale build-time addresses |
| Credential decryption fails | Matching `.secret_key` and database; whether data home changed |
| Tasks interrupted after restart | Resume the main task with `/resume` or continue a subagent in Tasks; avoid duplicating writes |
| Workbook version conflict | Read the latest workbook before editing again |
| Out of memory | Compatible prebuilt frontend, file size, process count, and logs |

For PM2, use `pm2 list` and `pm2 logs excelmanus-api --lines 50 --nostream`. For systemd, use `systemctl status excelmanus-api` and `journalctl -u excelmanus-api -n 50`. Remove credentials, file content, and other sensitive information before sharing logs.
