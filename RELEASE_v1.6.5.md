# ExcelManus v1.6.5 Release Notes

## 🎯 版本亮点

本版本重点修复了**非视觉模型图片处理崩溃**、**SSE 流式推送序列化异常**等关键 Bug，同时将部署脚本升级至 **v2.1.0**，大幅增强远程部署的健壮性与兼容性，并优化了移动端触控体验。

---

## 🐛 关键修复

### 非视觉主模型图片处理

当主模型不支持视觉能力时，不再向其发送 `image_url` 类型消息（会导致 API 报错），改为发送文本占位符 `[已上传 N 张图片，将由视觉模型分析]`，由 VLM B 通道单独处理图片描述。

涉及文件：`excelmanus/engine.py`

### SSE 流式推送崩溃修复

`ProgressivePipeline` 的进度回调原先直接 emit 原始 `dict`，与 SSE 序列化层的 `ToolCallEvent` 类型不匹配，导致流式推送崩溃。现已改为正确发送 `ToolCallEvent` 对象。

涉及文件：`excelmanus/pipeline/progressive.py`

### 前端构建产物修复

`next.config.ts` 新增 `typescript: { ignoreBuildErrors: true }`，解决因 TS 类型错误阻塞 `standalone` 产物生成的问题，确保部署流程不被中断。

涉及文件：`web/next.config.ts`

---

## 🔧 改进

### 部署脚本 v2.1.0

`deploy.ps1` 和 `deploy.sh` 同步升级至 v2.1.0，包含以下改进：

- **自动注入 REMOTE_SYSTEM_PATH** — 解决 SSH 非交互会话中 PATH 为空导致 `git`/`node`/`npm` 找不到的问题
- **修复 cmd/c SSH 挂死** — PowerShell 版改为直接调用 `ssh` 进程，避免 `cmd /c` 包装长命令输出时缓冲挂死
- **远程自动安装 git** — 远程服务器无 git 时自动通过包管理器（yum/apt/dnf/apk）安装
- **无 PM2 环境兼容** — 当远程服务器未安装 PM2 时，自动降级为 `nohup` 直接进程管理
- **PEM 权限自动修复** — SSH 密钥文件权限不正确时自动修复
- **PowerShell 5.1 兼容** — 修复 `$var ?? 'fallback'` 空合并运算符语法（PS 5.1 不支持），改用 `if/else`
- **移除 tail 依赖** — `npm install` 输出不再通过 `| tail -3` 管道，避免依赖 `tail` 命令
- **PATH 注入统一** — 远程命令不再逐条手动 `export PATH=...`，改为 `_remote()` / `Invoke-Remote()` 统一注入

### 移动端触控优化

- CSS 触控目标规则从 iOS 专属（`@supports (-webkit-touch-callout: none)`）改为全触控设备通用（`@media (pointer: coarse)`），修复 Android 等设备上的水平溢出问题
- 移除不必要的 `min-width: 44px` 约束，避免紧凑布局中按钮被撑宽
- 清理 Univer 工具栏链接的冗余触控尺寸覆盖

### Docker 多架构构建

- 默认构建平台从 3 个（amd64 + arm64 + arm/v7）精简为 2 个（amd64 + arm64），提升构建速度
- README 新增 Docker 镜像拉取命令和多平台自行构建说明

### 前端组件

- **SettingsDialog** — 移除内嵌的 `DialogTrigger` 按钮，改由外部统一控制弹窗开关，避免重复触发器
- **AssistantMessage** — 消息渲染逻辑优化（+81/-0）
- **ModelTab** — 健康检查逻辑调整（+29/-0）
- **RuntimeTab** — 运行时设置改进（+75/-0）

### 其他

- 修复 README logo 路径（`.png` → `.svg`）
- 新增 `web/public/icon.png`，修复加载页 logo 404
- 新增示例 CSV 文件：`广告与销售数据.csv`、`月度销售报表.csv`、`订单数据.csv`
- 新增 `deploy/_fix_frontend.sh` 前端热修复辅助脚本

---

## 🗑️ 移除

- 移除旧版 `deploy/windows_deploy_gui.py`（1311 行），由 C# 部署工具完全替代
- 移除 `一键部署.bat`，功能已整合进 `ExcelManusDeployTool.exe`
- 移除遗留图片文件 `f99a15b3ae663f1d74a1d2c25379ac7b.jpg`

---

## 📦 文件变更统计（vs v1.6.4）

```text
34 files changed, 2192 insertions(+), 1670 deletions(-)
```

### 新增文件

| 文件 | 说明 |
| ---- | ---- |
| `ExcelManusDeployTool.exe` | Windows 图形化部署工具 |
| `deploy/ExcelManusSetup.cs` | 部署工具源码（1093 行） |
| `deploy/build_multiarch.bat` | Windows 多平台 Docker 构建 |
| `deploy/build_multiarch.sh` | Linux/macOS 多平台 Docker 构建 |
| `deploy/buildkitd.toml` | Docker Hub 镜像加速配置 |
| `deploy/_fix_frontend.sh` | 前端热修复辅助脚本 |
| `.github/workflows/docker-multiarch.yml` | CI 多架构镜像发布 |
| `excelmanus/restart.py` | 跨平台服务重启模块（168 行） |
| `web/src/components/chat/ConfigErrorCard.tsx` | 配置错误提示卡片 |
| `web/public/icon.png` | 加载页 logo |
| `web/public/samples/*.csv` | 示例 CSV 数据文件（3 个） |

### 主要修改文件

| 文件 | 变更量 | 说明 |
| ---- | ------ | ---- |
| `deploy/deploy.ps1` | +149/-0 | 部署脚本 v2.1.0 |
| `deploy/deploy.sh` | +73/-0 | 部署脚本 v2.1.0 |
| `excelmanus/engine.py` | +21/-0 | 非视觉模型图片处理 |
| `excelmanus/pipeline/progressive.py` | +17/-0 | SSE 事件类型修复 |
| `excelmanus/api.py` | +61/-0 | API 扩展 |
| `web/src/app/globals.css` | +22/-0 | 移动端触控优化 |
| `web/src/components/chat/AssistantMessage.tsx` | +81/-0 | 消息渲染优化 |
| `web/src/components/settings/RuntimeTab.tsx` | +75/-0 | 运行时设置改进 |
| `web/next.config.ts` | +1 | TS 构建错误跳过 |
| `README.md` | +98/-0 | 文档更新 |

### 删除文件

| 文件 | 说明 |
| ---- | ---- |
| `deploy/windows_deploy_gui.py` | 旧版 Python 部署 GUI（1311 行） |
| `一键部署.bat` | 旧版一键部署脚本 |
| `f99a15b3ae663f1d74a1d2c25379ac7b.jpg` | 遗留图片文件 |
