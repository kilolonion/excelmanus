import { existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { browserPayloadDirectories, stageBrowser } from './stage-browser.mjs';
import { checkNodeRuntime } from './node-runtime.mjs';

const desktopRoot = resolve(fileURLToPath(new URL("..", import.meta.url)));
const projectRoot = resolve(desktopRoot, "..");
// Validate the runtime that will actually drive Playwright, not the host Node.
checkNodeRuntime(join(desktopRoot, '.build/runtime', process.platform === 'win32' ? 'node.exe' : 'node'));
const previewBuild = spawnSync(process.execPath, [join(projectRoot, "web/scripts/build-workbook-preview.mjs")], { cwd: projectRoot, stdio: "inherit" });
if (previewBuild.status !== 0) throw new Error("Workbook preview asset build failed");
const pythonCandidates = [
  process.env.EXCELMANUS_PYTHON,
  process.platform === "win32"
    ? join(projectRoot, ".venv", "Scripts", "python.exe")
    : join(projectRoot, ".venv", "bin", "python"),
  process.platform === "win32" ? "python.exe" : "python3",
  "python",
].filter(Boolean);

const python = pythonCandidates.find((candidate) =>
  candidate.includes("/") || candidate.includes("\\") ? existsSync(candidate) : true,
);
if (!python) throw new Error("找不到 Python。请设置 EXCELMANUS_PYTHON 或创建项目 .venv。");

const probe = spawnSync(python, ['-I', '-B', '-X', 'utf8', '-c', 'import platform; print(platform.machine().lower())'], {encoding:'utf8'});
const machine = probe.stdout?.trim();
const architecture = ({amd64:'x64',x86_64:'x64',aarch64:'arm64'})[machine] || machine;
if (probe.status !== 0 || architecture !== process.arch) throw new Error(`Backend Python architecture does not match Node: ${machine || probe.error}`);
const expected = readFileSync(join(projectRoot, "pyproject.toml"), 'utf8').match(/^version\s*=\s*"([^"]+)"/m)?.[1]?.trim();
const installed = spawnSync(python, ['-I', '-B', '-X', 'utf8', '-c', 'from importlib.metadata import version; print(version("excelmanus"))'], {encoding:'utf8', cwd: projectRoot}).stdout?.trim();
if (installed !== expected) throw new Error(`已安装的 excelmanus 元数据版本 (${installed}) 与 pyproject.toml (${expected}) 不一致，请先执行 uv sync`);
const browserRoot = join(desktopRoot, ".build", "playwright-browsers");
// Workbook previews use Playwright's default headless shell. Installing the
// headed browser as well duplicates hundreds of MB without serving a feature.
const browserBuild = spawnSync(python, ["-m", "playwright", "install", "--only-shell", "chromium"], {
  cwd: projectRoot, stdio: "inherit", env: { ...process.env, PLAYWRIGHT_BROWSERS_PATH: browserRoot },
});
if (browserBuild.status !== 0) throw new Error("Bundled Chromium installation failed");
browserPayloadDirectories(browserRoot);
const args = [
  "-m", "PyInstaller", "--noconfirm", "--clean",
  "--distpath", ".build/backend",
  "--workpath", ".build/work",
  "backend/excelmanus-backend.spec",
];
const result = spawnSync(python, args, { cwd: desktopRoot, stdio: "inherit" });
if (result.error) {
  throw new Error(`${python} 无法启动 PyInstaller: ${result.error.message}`);
}
if (result.status !== 0) {
  throw new Error(`PyInstaller 失败，退出码: ${result.status}`);
}
// Chromium is a separate executable with its own loader layout and signatures.
// PyInstaller must not rewrite its dylibs (libEGL has no load-command padding).
console.log('Bundled browser:', stageBrowser(browserRoot,
  join(desktopRoot, '.build/backend/excelmanus-backend/_internal/playwright-browsers')));
