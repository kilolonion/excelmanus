# 可重复发布打包

这份流程把桌面 EXE/DMG 和 Android APK 的构建、精简、验收、复制和校验值生成固定下来。以后不需要重新分析目录或手工判断哪些文件应该进入安装包；脚本只把构建所需的运行时和应用资源送进 electron-builder/Gradle。

## 一次性准备

在目标机器上准备：

- Node.js 22.12 或更高版本；
- uv、托管的 CPython 3.12，以及项目 `.venv`；
- Web 和 Desktop 的 npm 依赖；
- `.venv` 中的 PyInstaller 6.19–6.x；
- Android 构建需要 JDK 17 或更高版本、Android SDK Platform 35 和 Build Tools 35.0.0。

从仓库根目录执行：

```powershell
uv python install 3.12
uv sync --frozen --all-extras --dev --python 3.12
uv pip install --python .venv/Scripts/python.exe "PyInstaller>=6.19,<7"
npm --prefix web ci
npm --prefix desktop ci
```

macOS 将 Python 路径改为 `.venv/bin/python`：

```bash
uv python install 3.12
uv sync --frozen --all-extras --dev --python 3.12
uv pip install --python .venv/bin/python 'PyInstaller>=6.19,<7'
npm --prefix web ci
npm --prefix desktop ci
```

Android 需要在 `android/local.properties` 写入本机 SDK 路径，或设置 `ANDROID_HOME`/`ANDROID_SDK_ROOT`。`local.properties` 不入库。

## Windows

默认同时构建 Windows x64 安装程序和 Android debug/preview APK：

```powershell
.\scripts\package-release.ps1
```

只构建桌面或 Android：

```powershell
.\scripts\package-release.ps1 -Target desktop
.\scripts\package-release.ps1 -Target android
```

脚本默认先在临时交付目录完成全部构建，所有目标通过后才替换 `outputs/release/`；中途失败不会删除上一版有效产物。需要保留该目录中其他文件时：

```powershell
.\scripts\package-release.ps1 -KeepOutput
```

## macOS

在 macOS 本机执行，不能从 Windows 交叉生成 DMG：

```bash
bash scripts/package-release-macos.sh
```

只构建 DMG 或 Android：

```bash
bash scripts/package-release-macos.sh desktop
bash scripts/package-release-macos.sh android
bash scripts/package-release-macos.sh all keep
```

脚本通过同一个 `scripts/package-release.mjs` 编排器执行，所以 macOS 和 Windows 的版本读取、Python 精简、验收和校验值格式保持一致。

## 脚本做什么

桌面流程按以下顺序执行：

1. 从 `pyproject.toml` 读取版本；检查 Node、uv、`.venv`、PyInstaller 和本地 npm 依赖。
2. 运行 Desktop JavaScript 语法检查和 Next.js production build，然后只暂存 standalone 前端需要的文件。
3. 检查 `.build/runtime` 中的 Node 22.23.2。已有版本正确时直接复用；没有时调用项目的官方校验下载流程。网络不可用而本地运行时有效时不会重复下载。
4. 检查精简 Python 运行时。运行时只来自 `dependency-groups.desktop-runtime`，删除缓存/测试/GUI 内容，并拒绝 SciPy、sklearn、Seaborn、Plotly 等可选科学库。脚本把复用标记写在 `.build/.python-runtime.json`，不会把构建元数据放进安装包；后续依赖没有变化时直接复用。
5. 用 PyInstaller 冻结后端，执行桌面 runtime smoke（XLSX、DOCX、PNG、绘图、VBA、API 启停和前端 origin）。
6. 调用 electron-builder：Windows 生成 NSIS，macOS 生成 DMG。Windows 还运行真实 Electron supervisor smoke；macOS 对 `.app/Contents/Resources` 运行资源 smoke 和签名检查。

Android 流程会先运行桥接、局域网网关和配对的 Node 测试，然后执行：

```text
testDebugUnitTest lintDebug assembleDebug
```

脚本优先复用 `android/.gradle-home`、用户 Gradle 缓存中已解压的 Gradle 8.11.1；只有找不到本地分发时才让 wrapper 下载。这样 wrapper 的 zip 缓存不完整或网络受限时，仍能使用已解压的 Gradle 完成构建。

## 交付目录

默认交付目录是 `outputs/release/`，只包含：

- `ExcelManus-<version>-win-x64-setup.exe` 或 `ExcelManus-<version>-mac-<arch>.dmg`；
- `ExcelManus-<version>-android-debug.apk`；
- `SHA256SUMS.txt`；
- `release-manifest.json`。

Android 产物是 `com.excelmanus.android.debug` 的 debug/preview 包，适合本地安装验收；正式发布需要发布者配置固定签名并提高 `versionCode`。Windows 默认安装程序没有正式代码签名，公开分发前需要配置签名证书。macOS 使用仓库当前 electron-builder 签名配置，正式分发仍需 Developer ID 和 notarization。

## 常见问题

- **Node 下载超时**：脚本先检查 `.build/runtime/node(.exe)`；保留版本为 22.23.2 即可离线复用。首次构建且没有缓存时，需要允许访问 `nodejs.org` 后重试。
- **Python 运行时变大**：删除 `.build/.python-runtime.json` 后重跑脚本，会按当前锁文件重建并重新裁剪 Python 运行时。
- **找不到 PyInstaller**：使用项目 `.venv` 安装 `PyInstaller>=6.19,<7`，不要用系统 Python 代替构建环境。
- **Android 找不到 SDK**：设置 `ANDROID_HOME`/`ANDROID_SDK_ROOT`，或在 `android/local.properties` 中配置 `sdk.dir`。
- **不要直接复制 `desktop/.build`**：它包含构建中间文件；交付只使用 `outputs/release` 中的文件，桌面安装包内部由 electron-builder 的 `extraResources` 过滤实际运行时。

直接调用核心编排器也可以：

```bash
node scripts/package-release.mjs --platform windows
node scripts/package-release.mjs --platform macos
node scripts/package-release.mjs --platform android --android-only
```
