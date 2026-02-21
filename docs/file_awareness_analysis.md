# ExcelManus 文件感知问题深度分析与改进方案

**日期**: 2026-02-21
**关联会话**: conversation_20260221T135637_6b615c.json

---

## 一、问题确认：文件定位缺陷仍然存在

### 1.1 根因分析

经过完整代码审查，**问题一（文件定位代价高）确认仍然存在**。根因有三：

#### 缺陷 A：`inspect_excel_files` 不支持递归扫描

```python
# data_tools.py:1037-1045 — 仅扫描 directory 层级，不递归子目录
for ext in ("*.xlsx", "*.xlsm"):
    for p in safe_dir.glob(ext):       # ← 非递归 glob，不会进入子目录
        if p.name.startswith((".", "~$")):
            continue
        excel_paths.append(p)
```

当用户说"帮我处理学生花名册"，agent 调用 `inspect_excel_files(directory=".")` 只能看到根目录下的 `.xlsx` 文件。而目标文件 `.tmp/迎新活动排班表.xlsx` 位于子目录中，**直接不可见**。

#### 缺陷 B：无按 sheet 名称搜索的能力

`inspect_excel_files` 虽然会读取每个文件的 sheet names 并返回，但：
- 它**不接受 sheet 名称作为搜索条件**
- agent 必须先找到文件，才能看到 sheet 列表
- 当用户以 sheet 名而非文件名引用数据时（这是常见场景），工具链完全失效

#### 缺陷 C：`find_files` 已从工具集中移除

`file_tools.py` 中虽然实现了 `find_files(pattern, directory, max_results)` 函数，但在 Batch 5 精简中已从 `get_tools()` 导出列表删除（参见 `profile.py:18` 注释）。agent 无法通过工具直接按 glob 模式递归搜索文件。

### 1.2 实际影响链路

在问题会话中，agent 被迫经历如下昂贵路径：

```
inspect_excel_files(".") → 0 files found
    ↓
list_directory(depth=3) → 大量目录噪音
    ↓
run_code(os.walk) → 发现 8215 个 Excel 文件，但文件名不匹配
    ↓
ask_user → 用户选择"全盘按工作表名搜索"
    ↓
run_code(openpyxl 遍历 8215 文件的 sheet names) → 61.8 秒
```

**核心矛盾**：agent 没有一个能同时按「文件名 + sheet 名 + 列头」搜索的轻量级工具，只能退化到 `run_code` 做暴力全盘扫描。

---

## 二、业界产品的项目感知机制对比

### 2.1 Cursor：预建向量索引 + 增量同步

| 层次 | 实现 |
|------|------|
| **索引时机** | 项目打开时自动启动，每 10 分钟增量更新 |
| **索引方式** | 将代码分块（code chunking）→ 计算 embedding → 存入 Turbopuffer 向量数据库 |
| **增量机制** | 本地 Merkle Tree 对文件 hash 建树，与服务端比对，仅上传变更文件的块 |
| **查询时** | 用户查询 → 计算 query embedding → 向量近邻搜索 → 返回匹配文件路径+行号 → 本地读取原文 → 组装上下文 |
| **隐私** | 仅存储 embedding 和模糊化路径，不存储代码原文 |
| **成本** | 需要远程向量数据库（Turbopuffer），依赖 embedding 模型 |

**适用性评估**：Cursor 方案重度依赖向量数据库和 embedding 模型，对于 ExcelManus 来说过于复杂。但其"预建索引 + 增量更新"的理念值得借鉴。

### 2.2 Windsurf：RAG + Fast Context 子代理

| 层次 | 实现 |
|------|------|
| **索引方式** | 混合方案：AST 解析提取符号图 + 语义 embedding + RAG |
| **Fast Context** | 专门的检索子代理，使用 SWE-grep / SWE-grep-mini 模型 |
| **SWE-grep 工作流** | 最多 4 轮 × 每轮 8 路并行 grep/glob/read → 快速定位文件 |
| **核心优势** | 将"搜索"从主 agent 解耦为轻量子代理，避免污染主上下文 |
| **M-Query** | 自研检索技术，结合 LLM 智能查询改写 |

**适用性评估**：Fast Context 的"子代理 + 并行工具调用"思路非常适合 ExcelManus，因为 Excel 文件搜索本质上是 IO 密集型任务。

### 2.3 Claude Code：Explore 子代理 + 无预索引

| 层次 | 实现 |
|------|------|
| **索引方式** | **无预建索引**，完全依赖按需搜索 |
| **Explore 子代理** | 只读搜索专家，使用 Glob + Grep + Read + limited Bash |
| **工作模式** | 收到搜索任务 → 并行 glob 文件 → 并行 grep 内容 → 读取关键文件 → 汇报 |
| **上下文隔离** | 子代理独立上下文，搜索结果压缩后返回主代理 |
| **CLAUDE.md** | 项目级静态描述文件，提供持久化项目上下文 |
| **成本** | 无额外基础设施，仅消耗少量 token（使用 Haiku 等轻量模型） |

**适用性评估**：Claude Code 的方案最轻量，但 Excel 文件不能 grep，需要适配。其"无预索引 + 按需搜索 + 子代理隔离"的模式对 ExcelManus 有直接借鉴意义。

### 2.4 对比总结

| 维度 | Cursor | Windsurf | Claude Code | ExcelManus 现状 |
|------|--------|----------|-------------|-----------------|
| 预建索引 | ✅ 向量索引 | ✅ 混合索引 | ❌ 无索引 | ❌ 无索引 |
| 增量更新 | ✅ Merkle Tree | ✅ 自动 | N/A | N/A |
| 搜索方式 | 语义检索 | RAG + SWE-grep | Glob + Grep | 仅单层 glob |
| 搜索隔离 | 嵌入查询流程 | Fast Context 子代理 | Explore 子代理 | ❌ 主 agent 承担 |
| 并行搜索 | ✅ 服务端 | ✅ 8 路并行 | ✅ 并行工具调用 | ❌ 无 |
| 按内容搜索 | ✅ 语义相似 | ✅ grep + 语义 | ✅ grep | ❌ 不支持 |

---

## 三、ExcelManus 改进方案

### 3.1 设计原则

1. **轻量级**：不引入向量数据库或额外模型，保持 ExcelManus 的可部署性
2. **Excel 域特化**：针对 Excel 文件的特点（二进制、sheet 结构、列头语义）设计
3. **按需 + 缓存**：首次使用时构建，后续复用，避免浪费
4. **与现有架构兼容**：在现有工具体系内扩展，不大改架构

### 3.2 方案：Workspace Manifest（工作区清单）

#### 核心思路

在会话开始或首次需要文件发现时，**一次性递归扫描工作区所有 Excel 文件的轻量元数据**，构建一个紧凑的 JSON 清单（Manifest），缓存在内存中，并可注入 system prompt 或按需查询。

```
┌─────────────────────────────────────────────────────┐
│              Workspace Manifest                      │
│                                                      │
│  .tmp/迎新活动排班表.xlsx                             │
│    ├─ sheet: 学生花名册 (221×7)                      │
│    │    headers: [学号, 姓名, 班级, 角色, ...]        │
│    ├─ sheet: 排班总表 (50×5)                         │
│    │    headers: [日期, 时段, 负责人, ...]            │
│    └─ sheet: 签到记录 (0×0)                          │
│                                                      │
│  reports/月度汇总.xlsx                                │
│    ├─ sheet: 1月 (100×8)                             │
│    └─ sheet: 2月 (95×8)                              │
│                                                      │
│  ... (N files, compact JSON ~2-5KB)                  │
└─────────────────────────────────────────────────────┘
```

#### 3.2.1 Manifest 数据结构

```python
@dataclass
class SheetMeta:
    name: str              # sheet 名
    rows: int              # 行数
    columns: int           # 列数
    headers: list[str]     # 表头（前 N 列）

@dataclass
class ExcelFileMeta:
    path: str              # 相对路径（如 .tmp/迎新活动排班表.xlsx）
    name: str              # 文件名
    size_bytes: int        # 文件大小
    modified: str          # 最后修改时间
    sheets: list[SheetMeta]

@dataclass
class WorkspaceManifest:
    workspace_root: str
    scan_time: str         # 扫描时间
    total_files: int       # Excel 文件总数
    files: list[ExcelFileMeta]
```

#### 3.2.2 构建策略

```python
def build_manifest(workspace_root: str, *, max_files: int = 200) -> WorkspaceManifest:
    """递归扫描工作区，构建 Excel 文件清单。

    策略：
    1. rglob("*.xlsx") + rglob("*.xlsm") 递归发现所有 Excel 文件
    2. 跳过 .git/.venv/node_modules/outputs 等噪音目录
    3. 对每个文件用 openpyxl read_only 模式快速读取 sheet 元信息
    4. 仅读取每个 sheet 的前 1-2 行用于表头识别，不加载数据
    5. 整体耗时预期：200 文件 < 3 秒，8000 文件 < 30 秒
    """
```

#### 3.2.3 搜索接口

为 `inspect_excel_files` 增加搜索参数，**不破坏现有 API 签名**：

```python
def inspect_excel_files(
    directory: str = ".",
    max_files: int = 20,
    preview_rows: int = 3,
    max_columns: int = 15,
    include: list[str] | None = None,
    # ── 新增搜索参数 ──
    recursive: bool = True,          # 是否递归子目录（默认改为 True）
    search: str | None = None,       # 模糊搜索：匹配文件名 OR sheet 名 OR 列头
    sheet_name: str | None = None,   # 精确按 sheet 名搜索
) -> str:
```

当 `search="学生花名册"` 时，工具将：
1. 先查询 Manifest 缓存（如已构建）
2. 若未缓存，执行递归扫描并缓存
3. 在文件名、sheet 名、列头中做模糊匹配
4. 返回匹配的文件详情（不需遍历 8215 个文件的内容）

#### 3.2.4 Manifest 注入策略

借鉴 Cursor/Windsurf 的做法，将**紧凑版 Manifest 摘要**注入 system prompt：

```
## 工作区 Excel 文件概览
共 12 个 Excel 文件：
- .tmp/迎新活动排班表.xlsx → [学生花名册(221×7), 排班总表(50×5), 签到记录(0×0)]
- reports/月度汇总.xlsx → [1月(100×8), 2月(95×8)]
- ...
```

**预算控制**：
- 文件数 ≤ 20：完整注入（约 500-1500 字符），几乎不增加 token 成本
- 20 < 文件数 ≤ 100：仅注入文件路径 + sheet 名列表（约 2-4KB）
- 文件数 > 100：仅注入统计摘要 + 热点目录提示，搜索时按需查询 Manifest

#### 3.2.5 增量更新

借鉴 Cursor 的 Merkle Tree 思路，但大幅简化：

```python
def refresh_manifest(manifest: WorkspaceManifest) -> WorkspaceManifest:
    """增量更新：仅重新扫描 mtime 变化的文件。"""
    # 比对每个文件的 mtime，仅重读变更的文件元信息
    # 预期耗时：< 100ms（仅 stat() 调用）
```

### 3.3 实施路线图

#### Phase 1：最小可行改进（1-2 天）

| 变更 | 文件 | 说明 |
|------|------|------|
| `inspect_excel_files` 支持递归 | `data_tools.py` | `glob("*.xlsx")` → `rglob("*.xlsx")`，新增 `recursive` 参数 |
| `inspect_excel_files` 支持 `search` 参数 | `data_tools.py` | 在文件名 + sheet 名中模糊匹配 |
| 更新工具 schema | `data_tools.py` | 在 `get_tools()` 中增加新参数定义 |
| 更新 prompt 指引 | `10_core_principles.md` | 引导 agent 使用 `search` 参数 |

**效果**：上述会话场景从 7 步 61.8 秒 → **1 步 < 5 秒**。

#### Phase 2：Workspace Manifest 缓存层（3-5 天）

| 变更 | 说明 |
|------|------|
| 新增 `WorkspaceManifest` 模块 | `excelmanus/workspace_manifest.py` |
| 会话首轮自动构建 Manifest | 在 engine 初始化或首次工具调用时触发 |
| Manifest 摘要注入 system prompt | 在 `context_builder.py` 中增加 `_build_workspace_notice()` |
| 增量更新机制 | 基于 mtime 的轻量级 diff |

**效果**：agent 在首轮就知道工作区有哪些文件和 sheet，无需任何探查步骤。

#### Phase 3：搜索子代理（可选，5-7 天）

借鉴 Claude Code Explore / Windsurf Fast Context，创建轻量搜索子代理：

| 变更 | 说明 |
|------|------|
| 新增 `explorer` 子代理类型 | 专门负责文件发现和数据探查 |
| 使用轻量模型 | 类似 Claude Code 用 Haiku 做 Explore |
| 上下文隔离 | 搜索结果压缩后返回主 agent |

---

## 四、Phase 1 具体设计

### 4.1 `inspect_excel_files` 改造

```python
def inspect_excel_files(
    directory: str = ".",
    max_files: int = 20,
    preview_rows: int = 3,
    max_columns: int = 15,
    include: list[str] | None = None,
    recursive: bool = True,          # 新增
    search: str | None = None,       # 新增
    sheet_name: str | None = None,   # 新增
) -> str:
    """批量扫描目录下所有 Excel 文件（可递归），支持按文件名/sheet名/列头搜索。"""

    # 收集 Excel 文件
    excel_paths: list[Path] = []
    glob_method = safe_dir.rglob if recursive else safe_dir.glob
    for ext in ("*.xlsx", "*.xlsm"):
        for p in glob_method(ext):
            if p.name.startswith((".", "~$")):
                continue
            # 跳过噪音目录
            rel = p.relative_to(safe_dir)
            if any(part in _SKIP_DIRS for part in rel.parts[:-1]):
                continue
            excel_paths.append(p)

    # 如果指定了 search 或 sheet_name，先做快速过滤
    if search or sheet_name:
        matched = []
        search_lower = (search or "").lower()
        sheet_lower = (sheet_name or "").lower()
        for fp in excel_paths:
            # 文件名匹配
            if search_lower and search_lower in fp.name.lower():
                matched.append(fp)
                continue
            # 需要读取 sheet 信息
            try:
                wb = load_workbook(fp, read_only=True, data_only=True)
                for sn in wb.sheetnames:
                    if sheet_lower and sheet_lower in sn.lower():
                        matched.append(fp)
                        break
                    if search_lower and search_lower in sn.lower():
                        matched.append(fp)
                        break
                wb.close()
            except Exception:
                continue
        excel_paths = matched

    # ... 后续逻辑不变 ...
```

### 4.2 噪音目录跳过列表

```python
_SKIP_DIRS = frozenset({
    ".git", ".venv", "node_modules", "__pycache__",
    ".worktrees", "dist", "build",
})
```

### 4.3 工具 Schema 更新

```python
ToolDef(
    name="inspect_excel_files",
    description=(
        "批量扫描目录下所有 Excel 文件（默认递归子目录），一次返回每个文件的 sheet 列表、"
        "行列数、列名和少量预览行。支持按文件名、sheet 名模糊搜索，快速定位目标数据。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            # ... 保留现有参数 ...
            "recursive": {
                "type": "boolean",
                "description": "是否递归扫描子目录，默认 True",
                "default": True,
            },
            "search": {
                "type": "string",
                "description": "模糊搜索关键词，匹配文件名或 sheet 名（如 '学生花名册'）",
            },
            "sheet_name": {
                "type": "string",
                "description": "按 sheet 名称精确搜索，返回包含该 sheet 的文件",
            },
        },
    },
)
```

### 4.4 Prompt 更新

在 `10_core_principles.md` 中更新指引：

```markdown
1. **直接行动**：收到请求后立刻通过 Tool Calling 执行。
   当用户提到特定数据名称但未给出文件路径时，优先用
   `inspect_excel_files(search="关键词")` 递归搜索工作区，
   而非 `list_directory` 浏览目录。
```

---

## 五、预期收益

| 指标 | 改进前 | Phase 1 后 | Phase 2 后 |
|------|--------|-----------|-----------|
| 文件定位步骤 | 7 步 | 1 步 | 0 步（首轮注入） |
| 文件定位耗时 | 61.8 秒 | < 5 秒 | < 1 秒（缓存命中） |
| 工具调用次数 | 15 次 | ~8 次 | ~6 次 |
| Token 消耗 | 87,760 | ~50,000 | ~40,000 |
| 用户交互（ask_user） | 1 次（被迫） | 0 次 | 0 次 |
