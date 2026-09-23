#!/usr/bin/env node

// Reproducible release packaging for the Windows/macOS desktop app and the
// Android preview client.  Keep platform-specific policy here so the thin
// PowerShell and shell entry points cannot drift apart.
import { createHash } from "node:crypto";
import { execFileSync, spawnSync } from "node:child_process";
import {
  chmodSync,
  copyFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  renameSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const projectRoot = resolve(scriptDirectory, "..");
const desktopRoot = join(projectRoot, "desktop");
const androidRoot = join(projectRoot, "android");
const runtimeNodeVersion = "v22.23.2";
const gradleVersion = "8.11.1";
const requiredPythonModules = ["pandas", "openpyxl", "docx", "matplotlib", "PIL", "oletools"];
const forbiddenPythonModules = ["scipy", "sklearn", "seaborn", "plotly"];

function usage() {
  console.log(`ExcelManus release packaging

Usage:
  node scripts/package-release.mjs [options]

Options:
  --platform windows|macos   Desktop target; defaults to the host platform.
  --desktop-only             Build only the desktop installer.
  --android-only             Build only the Android debug APK.
  --skip-android             Do not build Android when building desktop.
  --output <directory>       Delivery directory (default: outputs/release).
  --no-clean-output          Keep unrelated files already in the delivery dir.
  --help                     Show this help.

The desktop target must be built on the matching operating system. Android
uses the repository Gradle wrapper and produces a signed debug/preview APK.
`);
}

function parseArguments(argv) {
  const hostDesktop = process.platform === "win32"
    ? "windows"
    : process.platform === "darwin"
      ? "macos"
      : null;
  const options = {
    desktop: hostDesktop,
    android: true,
    cleanOutput: true,
    output: join(projectRoot, "outputs", "release"),
  };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--help" || argument === "-h") {
      usage();
      process.exit(0);
    }
    if (argument === "--platform") {
      const value = argv[++index];
      if (!["windows", "macos", "android"].includes(value)) {
        throw new Error("--platform must be windows, macos, or android");
      }
      options.desktop = value === "android" ? null : value;
      options.android = value === "android" || options.android;
      continue;
    }
    if (argument === "--desktop-only") {
      if (!options.desktop) throw new Error("当前主机没有可用的桌面目标");
      options.android = false;
      continue;
    }
    if (argument === "--android-only") {
      options.desktop = null;
      options.android = true;
      continue;
    }
    if (argument === "--skip-android") {
      options.android = false;
      continue;
    }
    if (argument === "--output") {
      const value = argv[++index];
      if (!value) throw new Error("--output requires a directory");
      options.output = resolve(projectRoot, value);
      continue;
    }
    if (argument === "--no-clean-output") {
      options.cleanOutput = false;
      continue;
    }
    throw new Error(`未知参数: ${argument}`);
  }
  if (!options.desktop && !options.android) throw new Error("至少选择一个构建目标");
  if (options.desktop === "windows" && process.platform !== "win32") {
    throw new Error("Windows 安装包必须在 Windows 主机上构建");
  }
  if (options.desktop === "macos" && process.platform !== "darwin") {
    throw new Error("macOS DMG 必须在 macOS 主机上构建");
  }
  const outputRelative = relative(projectRoot, options.output);
  if (
    !outputRelative
    || outputRelative === ".."
    || outputRelative.startsWith(`..${sep}`)
    || isAbsolute(outputRelative)
  ) {
    throw new Error("交付目录必须位于仓库目录内");
  }
  return options;
}

function executable(command) {
  if (process.platform === "win32" && (command === "npm" || command === "npx")) {
    return `${command}.cmd`;
  }
  return command;
}

function commandLabel(command, args) {
  return `${command} ${args.join(" ")}`;
}

function run(command, args, cwd, label = commandLabel(command, args), environment = process.env) {
  console.log(`\n>>> ${label}`);
  const file = executable(command);
  const result = spawnSync(file, args, {
    cwd,
    env: environment,
    stdio: "inherit",
    shell: process.platform === "win32" && /\.(?:cmd|bat)$/i.test(file),
    windowsHide: true,
  });
  if (result.error) throw new Error(`${label} 启动失败: ${result.error.message}`);
  if (result.status !== 0) throw new Error(`${label} 失败，退出码 ${result.status}`);
}

function capture(file, args, cwd) {
  return execFileSync(file, args, {
    cwd,
    encoding: "utf8",
    env: process.env,
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  }).trim();
}

function readVersion() {
  const source = readFileSync(join(projectRoot, "pyproject.toml"), "utf8");
  const version = source.match(/^version\s*=\s*"([^"]+)"/m)?.[1];
  if (!version) throw new Error("pyproject.toml 中没有找到项目版本");
  return version;
}

function sha256File(file) {
  return createHash("sha256").update(readFileSync(file)).digest("hex");
}

function sha256Text(value) {
  return createHash("sha256").update(value).digest("hex");
}

function assertExists(file, message) {
  if (!existsSync(file)) throw new Error(`${message}: ${file}`);
}

function hostNodeCheck() {
  const [major, minor] = process.versions.node.split(".").map(Number);
  if (major < 22 || (major === 22 && minor < 12)) {
    throw new Error(`构建脚本需要 Node.js >= 22.12，当前为 ${process.version}`);
  }
}

function preflightDesktop() {
  hostNodeCheck();
  assertExists(join(desktopRoot, "node_modules", "electron-builder"), "请先执行 npm --prefix desktop ci");
  assertExists(join(projectRoot, "web", "node_modules", "next"), "请先执行 npm --prefix web ci");
  const python = process.platform === "win32"
    ? join(projectRoot, ".venv", "Scripts", "python.exe")
    : join(projectRoot, ".venv", "bin", "python");
  assertExists(python, "请先执行 uv sync --frozen --all-extras --dev --python 3.12");
  const pyinstaller = process.platform === "win32"
    ? join(projectRoot, ".venv", "Scripts", "pyinstaller.exe")
    : join(projectRoot, ".venv", "bin", "pyinstaller");
  assertExists(pyinstaller, "项目 .venv 中缺少 PyInstaller，请先安装 PyInstaller>=6.19,<7");
  try {
    const version = capture(python, ["-I", "-B", "-X", "utf8", "-c", "import PyInstaller; print(PyInstaller.__version__)"], projectRoot);
    console.log(`Build Python / PyInstaller: ${version}`);
  } catch (error) {
    console.warn(`无法在预检阶段启动 .venv Python，将由 prepare:backend 继续做最终检查: ${error.message}`);
  }
  try {
    console.log(`uv: ${capture("uv", ["--version"], projectRoot)}`);
  } catch (error) {
    throw new Error(`找不到 uv，请先安装 uv\n${error.message}`);
  }
}

function runtimeNodePath() {
  return join(desktopRoot, ".build", "runtime", process.platform === "win32" ? "node.exe" : "node");
}

function ensureNodeRuntime() {
  const target = runtimeNodePath();
  mkdirSync(dirname(target), { recursive: true });
  let version = null;
  if (existsSync(target)) {
    try {
      if (process.platform === "darwin") chmodSync(target, 0o755);
      version = capture(target, ["--version"], desktopRoot);
    } catch {
      version = null;
    }
  }
  if (version !== runtimeNodeVersion) {
    console.log(`bundled Node 不存在或版本不符（当前 ${version || "缺失"}），尝试按官方校验下载 ${runtimeNodeVersion}`);
    try {
      run(process.execPath, [join("scripts", "prepare.mjs"), "runtime"], desktopRoot);
    } catch (error) {
      console.warn(`Node 下载失败，将检查本地缓存是否仍可用: ${error.message}`);
    }
    if (existsSync(target)) {
      try {
        if (process.platform === "darwin") chmodSync(target, 0o755);
        version = capture(target, ["--version"], desktopRoot);
      } catch {
        version = null;
      }
    }
  } else {
    console.log(`复用已校验的 bundled Node ${version}`);
  }
  if (version !== runtimeNodeVersion) {
    throw new Error(`无法准备 bundled Node ${runtimeNodeVersion}。请在可访问 nodejs.org 的网络中重试 desktop/scripts/prepare.mjs runtime`);
  }
}

function runtimePythonPath() {
  return join(
    desktopRoot,
    ".build",
    "runtime",
    "python",
    process.platform === "win32" ? "python.exe" : "bin/python3",
  );
}

function pythonRuntimeFingerprint() {
  return sha256Text([
    readFileSync(join(projectRoot, "pyproject.toml"), "utf8"),
    readFileSync(join(projectRoot, "uv.lock"), "utf8"),
    process.platform,
    process.arch,
  ].join("\0"));
}

function probePythonRuntime(python) {
  if (!existsSync(python)) return null;
  const code = [
    "import importlib.util, json, sys",
    `required = ${JSON.stringify(requiredPythonModules)}`,
    `forbidden = ${JSON.stringify(forbiddenPythonModules)}`,
    "missing = [name for name in required if importlib.util.find_spec(name) is None]",
    "present_forbidden = [name for name in forbidden if importlib.util.find_spec(name) is not None]",
    "print(json.dumps({'python': sys.version.split()[0], 'missing': missing, 'forbidden': present_forbidden}))",
  ].join("; ");
  try {
    const result = JSON.parse(capture(python, ["-I", "-B", "-X", "utf8", "-c", code], desktopRoot));
    if (result.missing.length || result.forbidden.length) return result;
    const parent = process.platform === "win32" ? dirname(python) : dirname(dirname(python));
    const suspicious = [
      join(parent, "Lib", "tkinter"),
      join(parent, "lib", "python3.12", "tkinter"),
    ].some(existsSync);
    if (suspicious) return { ...result, missing: [], forbidden: ["tkinter"] };
    return result;
  } catch {
    return null;
  }
}

function writePythonRuntimeMarker(markerPath, fingerprint, probe) {
  writeFileSync(markerPath, `${JSON.stringify({
    schema: 1,
    fingerprint,
    platform: process.platform,
    arch: process.arch,
    python: probe.python,
    required: requiredPythonModules,
    excluded: [...forbiddenPythonModules, "tkinter", "__pycache__", "*.pyc"],
    generatedAt: new Date().toISOString(),
  }, null, 2)}\n`);
}

function ensurePythonRuntime() {
  const target = runtimePythonPath();
  // Keep build metadata outside extraResources; it must never ship inside the
  // installed interpreter.
  const marker = join(desktopRoot, ".build", ".python-runtime.json");
  const requirements = join(desktopRoot, ".build", "runtime", "python-requirements.txt");
  const fingerprint = pythonRuntimeFingerprint();
  const cleanupMetadata = () => {
    rmSync(requirements, { force: true });
    rmSync(join(desktopRoot, ".build", "runtime", ".python-runtime.json"), { force: true });
  };
  const probe = probePythonRuntime(target);
  let markerData = null;
  if (existsSync(marker)) {
    try {
      markerData = JSON.parse(readFileSync(marker, "utf8"));
    } catch {
      markerData = null;
    }
  }
  if (
    probe
    && !probe.missing.length
    && !probe.forbidden.length
    && markerData?.fingerprint === fingerprint
  ) {
    console.log(`复用已验证的精简 Python 运行时 ${probe.python}`);
    cleanupMetadata();
    return;
  }
  // A runtime created by the previous manual release flow has no marker. If
  // its required modules are present and forbidden optional modules are absent,
  // trust it once and create the marker; later builds become incremental.
  if (probe && !probe.missing.length && !probe.forbidden.length && existsSync(requirements) && !markerData) {
    console.log(`检测到已精简的 Python 运行时 ${probe.python}，写入可复用标记`);
    writePythonRuntimeMarker(marker, fingerprint, probe);
    cleanupMetadata();
    return;
  }
  console.log("准备锁定的 desktop-runtime Python 运行时，并删除缓存/测试/可选科学库");
  run(process.execPath, [join("scripts", "prepare-python.mjs")], desktopRoot);
  const after = probePythonRuntime(target);
  if (!after || after.missing.length || after.forbidden.length) {
    throw new Error(`精简 Python 运行时验收失败: ${JSON.stringify(after)}`);
  }
  writePythonRuntimeMarker(marker, fingerprint, after);
  cleanupMetadata();
}

function desktopBuilderPath() {
  const file = join(
    desktopRoot,
    "node_modules",
    ".bin",
    process.platform === "win32" ? "electron-builder.cmd" : "electron-builder",
  );
  assertExists(file, "找不到本地 electron-builder");
  return file;
}

function findNewest(files) {
  return files
    .filter(existsSync)
    .map(file => ({ file, time: statSync(file).mtimeMs }))
    .sort((a, b) => b.time - a.time)[0]?.file || null;
}

function findMacApp() {
  const dist = join(desktopRoot, "dist");
  const candidates = [];
  for (const entry of readdirSync(dist, { withFileTypes: true })) {
    if (!entry.isDirectory() || !entry.name.startsWith("mac")) continue;
    const app = join(dist, entry.name, "ExcelManus.app");
    if (existsSync(app)) candidates.push(app);
  }
  return findNewest(candidates);
}

function packageReport(target, installer) {
  const root = target === "windows"
    ? join(desktopRoot, "dist", "win-unpacked")
    : findMacApp();
  if (!root) throw new Error("找不到桌面包目录，无法生成包体报告");
  const output = join(desktopRoot, ".build", `package-report-${target}.json`);
  run(process.execPath, [
    join("scripts", "package-report.mjs"),
    root,
    "--installer",
    installer,
    "--output",
    output,
  ], desktopRoot);
}

function buildDesktop(target, version, artifacts) {
  preflightDesktop();
  run("npm", ["run", "check"], desktopRoot);
  run("npm", ["run", "build:web"], desktopRoot);
  run(process.execPath, [join("scripts", "prepare.mjs"), "frontend"], desktopRoot);
  ensureNodeRuntime();
  ensurePythonRuntime();
  run("npm", ["run", "prepare:backend"], desktopRoot);
  run("npm", ["run", "smoke"], desktopRoot);

  const buildStarted = Date.now();
  const builderArgs = target === "windows" ? ["--win", "nsis"] : ["--mac", "dmg"];
  run(desktopBuilderPath(), builderArgs, desktopRoot, `electron-builder ${builderArgs.join(" ")}`);

  let installer;
  if (target === "windows") {
    installer = join(desktopRoot, "dist", `ExcelManus Setup ${version}.exe`);
    assertExists(installer, "Windows 安装包没有生成");
    run(process.execPath, [join("scripts", "smoke-app.mjs"), join(desktopRoot, "dist", "win-unpacked", "ExcelManus.exe")], desktopRoot);
    packageReport(target, installer);
    const destination = join(options.output, `ExcelManus-${version}-win-x64-setup.exe`);
    copyFileSync(installer, destination);
    artifacts.push({ name: destination, kind: "desktop-installer", platform: "windows", arch: "x64" });
    return;
  }

  const dist = join(desktopRoot, "dist");
  const dmgCandidates = readdirSync(dist)
    .filter(name => name.toLowerCase().endsWith(".dmg"))
    .map(name => join(dist, name))
    .filter(file => statSync(file).mtimeMs >= buildStarted - 1000);
  installer = findNewest(dmgCandidates) || findNewest(readdirSync(dist).filter(name => name.toLowerCase().endsWith(".dmg")).map(name => join(dist, name)));
  assertExists(installer, "macOS DMG 没有生成");
  const app = findMacApp();
  assertExists(app, "macOS .app 没有生成");
  run(process.execPath, [join("scripts", "smoke.mjs"), join(app, "Contents", "Resources")], desktopRoot);
  packageReport(target, installer);
  const destination = join(options.output, `ExcelManus-${version}-mac-${process.arch}.dmg`);
  copyFileSync(installer, destination);
  artifacts.push({ name: destination, kind: "desktop-installer", platform: "macos", arch: process.arch });
}

function androidSdkPath() {
  const properties = join(androidRoot, "local.properties");
  if (existsSync(properties)) {
    const value = readFileSync(properties, "utf8").match(/^sdk\.dir=(.*)$/m)?.[1]?.trim();
    if (value) return value.replaceAll("\\:", ":").replaceAll("\\\\", "\\");
  }
  return process.env.ANDROID_HOME || process.env.ANDROID_SDK_ROOT || null;
}

function preflightAndroid() {
  assertExists(join(androidRoot, "gradlew"), "Android Gradle wrapper 缺失");
  const sdk = androidSdkPath();
  if (!sdk || !existsSync(resolve(projectRoot, sdk))) {
    throw new Error("找不到 Android SDK。请设置 ANDROID_HOME/ANDROID_SDK_ROOT，或在 android/local.properties 写入 sdk.dir");
  }
  const java = spawnSync("java", ["-version"], { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
  if (java.error || java.status !== 0) throw new Error("找不到 Java；Android 构建需要 JDK 17 或更高版本");
  console.log(`Android SDK: ${sdk}`);
}

function cachedGradleExecutable(home) {
  if (!home) return null;
  const root = join(home, "wrapper", "dists", `gradle-${gradleVersion}-bin`);
  if (!existsSync(root)) return null;
  const executableName = process.platform === "win32" ? "gradle.bat" : "gradle";
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    const candidate = join(root, entry.name, `gradle-${gradleVersion}`, "bin", executableName);
    if (existsSync(candidate)) return candidate;
  }
  return null;
}

function buildAndroid(version, artifacts) {
  preflightAndroid();
  run(process.execPath, [
    "--test",
    join("android", "tests", "bridge.test.cjs"),
    join("android", "tests", "lan-gateway.test.mjs"),
    join("android", "tests", "mobile-pairing.test.cjs"),
  ], projectRoot);
  const gradleArgs = ["testDebugUnitTest", "lintDebug", "assembleDebug"];
  const gradleEnvironment = { ...process.env };
  if (!gradleEnvironment.GRADLE_USER_HOME) {
    const projectCache = join(androidRoot, ".gradle-home");
    const userCache = process.platform === "win32"
      ? (process.env.USERPROFILE ? join(process.env.USERPROFILE, ".gradle") : null)
      : (process.env.HOME ? join(process.env.HOME, ".gradle") : null);
    const candidate = existsSync(projectCache) ? projectCache : userCache;
    if (candidate && existsSync(candidate)) gradleEnvironment.GRADLE_USER_HOME = candidate;
  }
  if (gradleEnvironment.GRADLE_USER_HOME) console.log(`Gradle cache: ${gradleEnvironment.GRADLE_USER_HOME}`);
  const gradleHomes = [
    gradleEnvironment.GRADLE_USER_HOME,
    process.platform === "win32" && process.env.USERPROFILE ? join(process.env.USERPROFILE, ".gradle") : null,
    process.platform !== "win32" && process.env.HOME ? join(process.env.HOME, ".gradle") : null,
  ].filter(Boolean).filter((home, index, all) => all.indexOf(home) === index);
  const cachedGradle = gradleHomes.map(cachedGradleExecutable).find(Boolean) || null;
  if (cachedGradle) console.log(`复用已解压的 Gradle ${gradleVersion}: ${cachedGradle}`);
  if (process.platform === "win32") {
    run(cachedGradle || join(androidRoot, "gradlew.bat"), gradleArgs, androidRoot, `${cachedGradle ? "gradle" : "gradlew.bat"} ${gradleArgs.join(" ")}`, gradleEnvironment);
  } else {
    if (cachedGradle) {
      run(cachedGradle, gradleArgs, androidRoot, `gradle ${gradleArgs.join(" ")}`, gradleEnvironment);
    } else {
      run("bash", ["gradlew", ...gradleArgs], androidRoot, `bash gradlew ${gradleArgs.join(" ")}`, gradleEnvironment);
    }
  }
  const apk = join(androidRoot, "app", "build", "outputs", "apk", "debug", "app-debug.apk");
  assertExists(apk, "Android debug APK 没有生成");
  const destination = join(options.output, `ExcelManus-${version}-android-debug.apk`);
  copyFileSync(apk, destination);
  artifacts.push({
    name: destination,
    kind: "android-apk",
    platform: "android",
    arch: "universal",
    applicationId: "com.excelmanus.android.debug",
    variant: "debug/preview",
  });
}

function writeManifest(version, artifacts, options) {
  const finalized = artifacts.map(artifact => {
    const bytes = statSync(artifact.name).size;
    return { ...artifact, name: relative(projectRoot, artifact.name).replaceAll("\\", "/"), bytes, sha256: sha256File(artifact.name) };
  });
  const manifest = {
    schema: 1,
    version,
    generatedAt: new Date().toISOString(),
    host: { platform: process.platform, arch: process.arch, node: process.version },
    desktopRuntime: {
      source: "dependency-groups.desktop-runtime",
      bundledNode: runtimeNodeVersion,
      excluded: [...forbiddenPythonModules, "tkinter", "__pycache__", "*.pyc", "*.map", "*.d.ts"],
    },
    artifacts: finalized,
  };
  writeFileSync(join(options.output, "release-manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`);
  writeFileSync(
    join(options.output, "SHA256SUMS.txt"),
    `${finalized.map(artifact => `${artifact.sha256}  ${artifact.name.split("/").at(-1)}`).join("\n")}\n`,
  );
  console.log(`\n交付目录: ${options.output}`);
  for (const artifact of finalized) console.log(`  ${artifact.name} (${artifact.bytes} bytes, ${artifact.sha256})`);
}

const options = parseArguments(process.argv.slice(2));
const version = readVersion();
const finalOutput = options.output;
const stagingOutput = options.cleanOutput
  ? join(dirname(finalOutput), `.release-staging-${process.pid}`)
  : finalOutput;
options.finalOutput = finalOutput;
options.output = stagingOutput;
if (options.cleanOutput && existsSync(stagingOutput)) rmSync(stagingOutput, { recursive: true, force: true });
mkdirSync(stagingOutput, { recursive: true });
console.log(`ExcelManus ${version} release packaging`);
console.log(`desktop=${options.desktop || "skip"}, android=${options.android}, staging=${stagingOutput}`);
const artifacts = [];

try {
  if (options.desktop) buildDesktop(options.desktop, version, artifacts);
  if (options.android) buildAndroid(version, artifacts);
  if (options.cleanOutput) {
    if (existsSync(finalOutput)) rmSync(finalOutput, { recursive: true, force: true });
    renameSync(stagingOutput, finalOutput);
    for (const artifact of artifacts) artifact.name = join(finalOutput, artifact.name.split(sep).at(-1));
    options.output = finalOutput;
  }
  writeManifest(version, artifacts, options);
} catch (error) {
  if (options.cleanOutput) {
    console.error(`\n打包失败，旧交付目录未修改。临时目录保留在: ${stagingOutput}`);
  }
  throw error;
}
