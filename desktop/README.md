# ExcelManus Desktop

Desktop packaging for the existing FastAPI backend and Next.js frontend. Applies to the 1.8.0 source tree; updated on 2026-09-19.

[Project overview](../README_EN.md) · [Documentation](../docs/README.md) · [Operations](../docs/ops-manual_en.md)

## What the package contains

The desktop app starts local services on loopback ports and displays the Web UI in Electron. It includes:

- A PyInstaller backend executable.
- A relocatable Python runtime and dependencies for `run_code`.
- The Next.js standalone frontend and static assets.
- A Node.js runtime for the frontend server.

Users of a complete package do not need the developer checkout or a system Python installation. Additional MCP commands still need their executables: the bundled Node binary does not include npm or npx.

The repository defines macOS DMG and Windows NSIS targets. Installer availability, signing, and tested architectures depend on the individual release; a packaging target is not proof of platform acceptance.

## App experience

The packaged app launches the local backend and frontend for the user, then opens the same ExcelManus workspace used by the Web interface. Conversations, workspace files, background tasks, spreadsheet editing, and model settings stay inside one application window.

<p align="center">
  <img src="../docs/images/webui-desktop.png" width="960" alt="ExcelManus desktop workspace" />
</p>

<table>
<tr>
<td width="50%"><img src="../docs/images/webui-mobile.png" alt="Spreadsheet editing in ExcelManus Desktop" /></td>
<td width="50%"><img src="../docs/images/app-settings.png" alt="Model settings in ExcelManus Desktop" /></td>
</tr>
<tr>
<td align="center"><b>Spreadsheet workspace</b><br />Workbook editing and conversation in one view</td>
<td align="center"><b>Model settings</b><br />Providers, model profiles, subscriptions, and OAuth</td>
</tr>
</table>

On first launch, add or connect a model provider in Settings and choose the active model. The app keeps its own profile by default, so it does not silently merge an existing source installation. Additional MCP commands may still require executables installed on the host.

## Build prerequisites

Use the target operating system and architecture. Python, NumPy, Pillow, cryptography, and other dependencies contain native components; do not cross-package a macOS environment into a Windows installer.

- Node.js 22.12 or newer for the build scripts.
- uv with a managed CPython 3.12 installation.
- Project dependencies and PyInstaller in the build environment.

From the repository root:

```bash
uv python install 3.12
uv sync --frozen --all-extras --dev --python 3.12
npm --prefix web ci
npm --prefix desktop ci
```

Install PyInstaller into that environment on macOS:

```bash
uv pip install --python .venv/bin/python 'PyInstaller>=6.19,<7'
```

On Windows PowerShell:

```powershell
uv pip install --python .venv/Scripts/python.exe "PyInstaller>=6.19,<7"
```

The backend build prefers the project `.venv`. `EXCELMANUS_PYTHON` can select another build interpreter, but the bundled code runtime is prepared separately from uv's managed Python distribution.

## Build an installer

From `desktop/`:

```bash
npm run check
npm run dist:mac   # On macOS
npm run dist:win   # On Windows
```

Run only the command for the current platform. Both commands rebuild the Web UI, stage the frontend and runtimes, package the backend, and invoke electron-builder. Artifacts are written to `desktop/dist/`. Staging synchronizes the desktop package and lockfile version with `pyproject.toml`.

For troubleshooting, the individual stages are available:

```bash
npm run prepare:assets
npm run prepare:backend
```

`prepare:assets` includes the Web build and Python runtime preparation. Calling `prepare:frontend` and `prepare:runtime` alone does not produce a complete package.

The staging script currently selects the official Node.js v22.23.2 distribution and checks it against the upstream SHA-256 manifest. The Python runtime is copied from uv's managed distribution and populated from `uv.lock`, including web, analysis, and VBA dependencies. The frozen backend executable must not be used as a Python interpreter for `run_code`.

## Validate the packaged runtime

From `desktop/`:

```bash
npm run smoke
```

This checks `.build` with developer tools removed from PATH. It covers frozen imports, workbook creation, pandas reading through bundled Python, frontend runtime origin, CORS, and model-profile persistence. It makes no language-model calls. Backend startup may connect to the default Exa MCP server; remote MCP availability is not a smoke-test acceptance condition.

To check the bytes inside a macOS app instead of staging files:

```bash
node scripts/smoke.mjs 'dist/mac-arm64/ExcelManus.app/Contents/Resources'
```

Adjust the path for the actual platform output. These checks do not replace installing the app, opening its UI, exercising the model workflow, or checking platform-specific signing. The desktop workflow uploads build artifacts; it does not itself publish a signed GitHub Release.

## Signing and distribution

macOS builds currently use ad-hoc signing (`identity: "-"`) for local validation. This is different from Developer ID signing and notarization. Configure the owner's Developer ID identity and notarization credentials for a notarized release; the repository's default build does not establish that status.

Build and test Windows installers on Windows. Configure code signing separately when distributing signed executables; signing does not guarantee the absence of reputation-based OS prompts.

## Data, logs, and upgrades

The default durable profile is `profile/` inside Electron's user-data directory. The app passes this as `EXCELMANUS_HOME` to the backend. The default workspace is `profile/data/`, and logs are written to `profile/logs/desktop.log`. Installation resources and `app.asar` are treated as read-only.

`EXCELMANUS_HOME` can explicitly select another profile. Existing `~/.excelmanus` data is not silently copied or merged. Back up the database, `.secret_key`, and workspace files before moving a profile, and do not run another ExcelManus service on it concurrently. Registered workspaces outside the profile need separate backups.

The app selects available ports at launch and reuses the frontend port when possible to preserve browser-local preferences. Configuration restarts drain the API and ask Electron to relaunch the backend on the same port.

Source-tree Git update, backup-restore, and deployment controls are disabled in desktop mode. Upgrade by installing a new app bundle while retaining the profile. This is separate from restoring a workbook revision inside the workspace.

## Windows lifecycle and acceptance

Windows builds currently target **x64**. Build scripts reject mixed Python/Node
architectures; ARM64 is not advertised as a validated native target. All Python
path queries explicitly use `-I -B -X utf8`, including under Chinese build paths.

The desktop owns the sidecars' stdin pipes. Normal quit sends `shutdown`, drains
the API, and waits for exit; only an expired grace period falls back to a checked
system `taskkill.exe /T /F`. Failed termination is reported and process references
are retained. Next's graceful shutdown handler is reached through a small runner
outside app.asar (ordinary Node cannot load an asar file).

The frozen Windows backend clears inherited SetDllDirectory state once at startup
and retains AddDllDirectory handles for its own later imports. It owns a Windows
Job Object with KILL_ON_JOB_CLOSE, in addition to MCP SDK connection jobs, so the
last cleanup does not depend on Unix `ps` or recycled PIDs. Key files use the
current token SID and a verified protected DACL; ACL failure is fatal to key use,
and permissions are applied before key bytes are written.

`npm test` checks graceful/forced quit and failed termination. The Windows CI:

1. Builds under a Chinese checkout and managed-Python directory.
2. Tests the actual `dist/win-unpacked/resources`.
3. Installs NSIS into a directory containing Chinese text and spaces.
4. Runs the installed Electron app, changes a setting, checks same-port restart
   and persisted settings, and requests normal exit through its inherited pipe.
5. Reinstalls, repeats the checks, uninstalls, and checks the profile survives.

For local installed-app acceptance:

```powershell
node scripts/smoke-app.mjs 'C:\Path With Spaces\ExcelManus.exe'
```

The backend manifest opts into long paths. Windows long-path behavior still
requires the machine policy to permit it; prefer a short local installation path
if the policy is disabled. This build does not silently modify user registry
policy. The included shell tool does not install Unix utilities or evaluate
PowerShell aliases: use list_directory/read_text_file and native spreadsheet tools
for those operations; Windows argument parsing preserves backslashes.

Tag builds and manual `signed_windows=true` runs require repository secrets
`WINDOWS_CSC_LINK` and `WINDOWS_CSC_KEY_PASSWORD`; they fail rather than producing
an unsigned release when signing is required. Manual test builds can remain
unsigned. The CI also checks the installer's Authenticode status for signed runs.
Credentials are not stored in this repository.
