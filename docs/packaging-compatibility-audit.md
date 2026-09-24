# 打包与 Windows 安装优化兼容性复查

日期：2026-09-24。基于当前工作区，主机为 macOS arm64；不代表 Windows 原生验收已经完成。

## 最终默认策略

| 优化 | 最终处理 | 兼容性依据 |
| --- | --- | --- |
| 仅打包 Chromium headless shell | 保留 | 产品预览调用 `launch(headless=True)`；最终应用包实际生成普通、中文及特殊字符工作簿截图 |
| Playwright 与前端共用 Node | 保留 | 检查 Node 的精确版本、系统、架构；实际冻结驱动、截图及前端服务通过 |
| 删除重复安装包缓存复制 | 保留 | 当前完整下载安装使用应用自己的目录，不读取 electron-updater 的 `installer.exe` 缓存 |
| 同盘提取与子目录移动 | 保留 | 共享父目录逐层合并，冲突文件才复制；保留用户文件，遇到目录链接或持久错误中止 |
| Python 标准库 ZIP | **默认关闭** | 虚拟 `__file__` 不完全兼容任意用户脚本；默认保留真实源码路径 |

可选 ZIP 模式仍可通过 `EXCELMANUS_COMPACT_PYTHON_STDLIB=1` 启用。已修复代码与资源分处 ZIP/磁盘造成 `pkgutil.get_data`、`importlib.resources` 读取失败的问题：带资源、原生文件或 namespace 布局的包整包留在磁盘。模式开关及准备/验收脚本内容参与缓存指纹。

## 本轮发现并修复

1. **标准库资源访问回归**：在旧 ZIP 方案中真实复现 `ctypes.macholib/README.ctypes` 读取失败。修正选包策略，并为默认包恢复普通 `module.__file__` 行为。
2. **NSIS 编解码器不匹配**：项目锁定 electron-builder 26.15.3；其上游报告现代 7za 默认 BCJ2 会让安装插件静默跳过 PE 文件。Windows afterPack 强制 BCJ，使 npm、发布脚本及直接 builder 入口一致。[上游记录](https://github.com/electron-userland/electron-builder/issues/9983)
3. **解压不完整仍可能继续**：在移动前，对直接嵌入安装器的可信文件清单逐项检查，缺文件或被目录替代立即失败。这个快速检查验证存在性和类型，不声称验证内容哈希。
4. **安装验收覆盖不足**：Windows 验收在全新安装和升级后，对 `win-unpacked` 所有文件逐个比较大小与 SHA-256，再运行安装目录中的运行时、截图和真实 Electron 监督进程。全量哈希只发生在验收，不增加用户安装开销。
5. **缓存运行时架构检查不完整**：原先只比 Node 版本；现同时检查 OS/架构，并在打包前和最终资源验收时执行。
6. **主机浏览器缓存可能掩盖缺包**：冻结后端要求随包浏览器与 Node 存在；不再回退到主机 Playwright 缓存。
7. **预览数据默认编码依赖 Windows 代码页**：观察数据和截图元数据读写显式使用 UTF-8，实际截图验收加入中文、欧元符号与 emoji。
8. **发布目录切换失败窗口**：先完成产物哈希和清单，再备份旧目录并重命名新目录。替换失败会恢复旧版；恢复也失败则保留旧版备份并报告路径。已覆盖文件锁与恢复失败回归。
9. **验收误选旧 EXE**：安装与包体报告按 package.json 的确切版本选择安装包，不再使用目录中第一个匹配项。

前一轮发现的 Chromium 动态库被 PyInstaller 改写、openpyxl 版本元数据遗漏也已修复：浏览器在冻结完成后单独暂存，包中保留必要的 dist-info。

## 已执行验证

- Desktop Node 测试：57 通过，3 项 Windows 原生测试在 macOS 跳过。
- Python 桌面、打包与 release-check 测试：36 通过，1 项 Windows 原生测试跳过。
- 工作簿预览回归：3 通过。
- 默认 Python 运行时：资源读取、源码路径、多进程 spawn、中文编码、SSL、SQLite、压缩、CLI、XLSX/DOCX/绘图通过。
- 最终 macOS 应用包：代码签名校验、离线截图、HTTP/CORS、真实 Electron 启动、同端口重启、设置持久化、正常退出通过。
- NSIS：活动页/提取、性能 fixture，以及完整生产安装器和卸载器编译通过。macOS 使用原生 NSIS 3.12 编译器配合 builder 的模板/资源；这不能替代 Windows 上锁定编译器与插件的执行验收。
- JavaScript 语法、发布入口帮助命令及改动空白检查通过。

证据在 `desktop/.build/desktop-audit-tests.log`、`python-runtime-default-audit.log`、`packaged-audit.log`、`nsis-audit-compile.log`、`nsis-audit-full.log`。最终应用包位于 `desktop/.build/package-audit/mac-arm64/ExcelManus.app`，包体报告为同目录上层的 `package-report.json`。

## 边界与发布条件

- **Windows 真实安装、升级、卸载、文件锁和目录链接回归仍待运行。** 已接入 `.github/workflows/desktop-build.yml`，但本次没有执行或声称通过远程 CI。
- 需观察 Defender 正常开启时的 `dist/install-timings.json`，在同一机器/磁盘上比较，不能从 macOS 包体推导 Windows 节时比例。
- 用户目录共享路径保留、清单移除和权限检查继续有效；安装被中断或新文件失败时会中止，但现有 NSIS 升级流程并非整包事务回滚，旧程序可能已移除，需要重跑安装器。
- 主程序/浏览器/原生库仍须满足发行平台要求；代码签名和 macOS notarization 的正式发布配置未在本次改变。
- SciPy、sklearn、Seaborn、Plotly 等可选科学库未因本轮裁剪新增排除，它们原本就不属于桌面独立运行时组。它们不能算作本轮提速收益或宣称已经随包支持。
- 同版本 macOS 组件样本中，完整版 Chromium 与重复 Node 共约 474.7 MiB；这是被避免打包的冗余组件量，不是 Windows 实测节省，也不是相对旧发行版安装包的压缩体积差。

结论：保留有实际功能验证的去重和安装 I/O 优化，默认撤回标准库 ZIP 的行为变化；Windows 正式发布仍以原生 CI 验收为条件。
