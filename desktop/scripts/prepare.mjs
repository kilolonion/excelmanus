import { chmodSync, copyFileSync, cpSync, existsSync, mkdirSync, readdirSync, rmSync, readFileSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { join, resolve } from "node:path";
import { frontendRuntimeFilter } from './runtime-files.mjs';

const desktopRoot = resolve(fileURLToPath(new URL("..", import.meta.url)));
const projectRoot = resolve(desktopRoot, "..");
const buildRoot = join(desktopRoot, ".build");

function resetDir(path) {
  rmSync(path, { recursive: true, force: true });
  mkdirSync(path, { recursive: true });
}

function requireDir(path, message) {
  if (!existsSync(path)) throw new Error(`${message}: ${path}`);
}

function stageFrontend() {
  const standalone = join(projectRoot, "web", ".next", "standalone");
  const staticDir = join(projectRoot, "web", ".next", "static");
  const publicDir = join(projectRoot, "web", "public");
  requireDir(standalone, "Next standalone 产物不存在，请先执行 npm --prefix web run build");
  requireDir(staticDir, "Next static 产物不存在，请先执行 npm --prefix web run build");
  requireDir(publicDir, "web/public 不存在");

  const destination = join(buildRoot, "frontend");
  resetDir(destination);
  // electron-builder intentionally filters directories named node_modules from
  // extraResources. Keep the standalone dependency tree under a different
  // name and expose it through NODE_PATH at runtime. Copy directly to that
  // name: renaming a just-copied dependency tree can fail with EPERM on Windows
  // while the indexer or antivirus still has one of its descendants open.
  for (const entry of readdirSync(standalone)) {
    cpSync(join(standalone, entry), join(destination, entry === "node_modules" ? "next_modules" : entry), {
      recursive: true, filter: file => frontendRuntimeFilter(standalone, file),
    });
  }
  mkdirSync(join(destination, ".next"), { recursive: true });
  cpSync(staticDir, join(destination, ".next", "static"), { recursive: true });
  cpSync(publicDir, join(destination, "public"), { recursive: true });
  const version = readFileSync(join(projectRoot, "pyproject.toml"), "utf8").match(/^version\s*=\s*"([^"]+)"/m)?.[1];
  if (!version) throw new Error("Missing project version");
  for (const filename of ["package.json", "package-lock.json"]) {
    const file = join(desktopRoot, filename);
    const pkg = JSON.parse(readFileSync(file, "utf8"));
    pkg.version = version;
    if (pkg.packages?.[""]) pkg.packages[""].version = version;
    writeFileSync(file, JSON.stringify(pkg, null, 2) + "\n");
  }
  copyFileSync(join(desktopRoot, "src", "frontend-runner.cjs"), join(buildRoot, "frontend-runner.cjs"));
  execFileSync(process.execPath, [join(projectRoot, "web", "scripts", "gen-splash.mjs"), join(buildRoot, "splash.html")], { stdio: "inherit" });
  console.log(`staged frontend: ${destination}`);
}

async function stageRuntime() {
  const destination = join(buildRoot, "runtime");
  mkdirSync(destination, { recursive: true });
  // Do not copy process.execPath: Homebrew Node depends on /opt/homebrew dylibs.
  const version = "22.23.2";
  const platform = process.platform;
  if (!["darwin", "win32"].includes(platform)) throw new Error("Build on macOS or Windows");
  const base = `https://nodejs.org/dist/v${version}`;
  const filename = platform === "win32" ? `win-${process.arch}/node.exe` : `node-v${version}-darwin-${process.arch}.tar.gz`;
  const get = async (url) => {
    const response = await fetch(url, { signal: AbortSignal.timeout(120_000) });
    if (!response.ok) throw new Error(`Download failed: ${url} (${response.status})`);
    return Buffer.from(await response.arrayBuffer());
  };
  const sums = (await get(`${base}/SHASUMS256.txt`)).toString("utf8");
  const expected = sums.split("\n").map((line) => line.trim().split(/\s+/)).find(([, name]) => name === filename)?.[0];
  if (!expected) throw new Error(`No official checksum for ${filename}`);
  const bytes = await get(`${base}/${filename}`);
  if (createHash("sha256").update(bytes).digest("hex") !== expected) throw new Error("Node checksum mismatch");
  const target = join(destination, platform === "win32" ? "node.exe" : "node");
  if (platform === "win32") writeFileSync(target, bytes);
  else {
    const archive = join(buildRoot, "node.tar.gz");
    writeFileSync(archive, bytes);
    const unpack = join(buildRoot, "node-unpack");
    resetDir(unpack);
    execFileSync("tar", ["-xzf", archive, "-C", unpack]);
    copyFileSync(join(unpack, `node-v${version}-darwin-${process.arch}`, "bin", "node"), target);
    chmodSync(target, 0o755);
  }
  execFileSync(target, ["--version"], { stdio: "inherit" });
}

const mode = process.argv[2] || "all";
if (mode === "frontend" || mode === "all") stageFrontend();
if (mode === "runtime" || mode === "all") await stageRuntime();
if (!["frontend", "runtime", "all"].includes(mode)) {
  throw new Error(`unknown prepare mode: ${mode}`);
}
