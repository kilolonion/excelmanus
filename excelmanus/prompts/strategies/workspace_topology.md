---
name: workspace_topology
version: "1.0.0"
priority: 15
layer: strategy
max_tokens: 500
conditions: {}
---
## 工作区存储拓扑

工作区根目录 `{workspace_root}` 包含以下特殊目录，每个目录有明确的语义角色：

### 目录结构

| 目录 | 用途 | 读写权限 |
|------|------|----------|
| `.` (根目录) | 用户的原始 Excel 文件和项目文件 | 受备份保护（开启时写入重定向） |
| `uploads/` | 用户通过前端上传的附件（Excel、图片、CSV 等） | 只读引用，写入时复制到根目录或 outputs/ |
| `outputs/` | 所有 agent 产出物的总目录 | 可自由写入 |
| `outputs/backups/` | 备份模式下的工作副本（staged files） | 自动管理，勿手动操作 |
| `outputs/.versions/` | 文件版本快照（支持精确回滚） | 自动管理，勿手动操作 |

### uploads/ 目录规则

- 用户上传的文件存储在 `uploads/` 下，文件名格式为 `{8位hex}_{原始文件名}`（如 `a1b2c3d4_销售数据.xlsx`）
- 引用上传文件时使用完整路径（如 `./uploads/a1b2c3d4_销售数据.xlsx`），但向用户展示时使用去掉前缀的原始文件名
- uploads/ 中的 Excel 文件已包含在工作区文件概览中，可直接用 `read_excel` 等工具操作
- uploads/ 中的非 Excel 文件（图片、PDF 等）可通过 `run_code` 读取处理

### 备份与版本管理

- **备份模式开启时**：所有对根目录文件的写入自动重定向到 `outputs/backups/` 下的工作副本，原始文件不被修改
- **版本追踪**：每次写入前自动保存原始版本快照到 `outputs/.versions/`，支持 `/backup apply`（应用修改到原文件）和 `/backup rollback`（丢弃修改）
- **CoW（写时复制）**：当文件受保护时，系统自动创建副本并维护路径映射，后续操作使用副本路径

### 浏览工作区

- 使用 `list_directory(mode="tree")` 查看递归目录树结构
- 使用 `list_directory(mode="flat")` 查看扁平文件列表（支持分页）
- 使用 `list_directory(mode="overview")` 查看目录统计摘要（文件类型分布、热点目录）
- 浏览 uploads/ 时用 `list_directory(directory="uploads")`
- 浏览输出产物时用 `list_directory(directory="outputs")`
