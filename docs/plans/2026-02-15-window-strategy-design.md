# 窗口感知层优化：类型策略模式 + ASCII 标记

> 日期：2026-02-15
> 状态：设计中
> 范围：`excelmanus/window_perception/`

## 1. 问题背景

### 1.1 explorer 窗口在 unified 模式下信息丢失

当 LLM 调用 `list_directory` 或 `scan_excel_files` 时，工具函数正确返回了 JSON 结果，
但窗口感知层的 unified 模式将原始结果替换为一行确认摘要：

```
✅ [explorer_1: 未知文件 / 未知Sheet] list_directory: - | 0行×0列 | enriched | 意图=general
```

LLM 看到 `0行×0列` 后判定目录为空，直接回复用户，不再触发下一轮。

根因链：
1. `ingest_and_confirm()` 对所有窗口类型走同一条 unified 路径
2. `_apply_ingest()` 对 EXPLORER 类型直接 return，不设置 `total_rows`/`total_cols`
3. `build_confirmation_record()` 读取 `window.total_rows`（为 0），生成 `0行×0列`
4. 确认文本完全替代原始 JSON，LLM 当前轮丢失所有目录信息

### 1.2 WindowState 模型是 sheet-centric 的

核心字段（viewport, data_buffer, cached_ranges, columns, schema）全是 Excel 概念。
explorer 数据被塞进 `metadata["entries"]`，是非结构化的 hack。

### 1.3 确认协议一刀切

所有窗口类型共用 `build_confirmation_record`，格式 `行×列` 对 explorer 无意义。

### 1.4 _resolve_target_window 对 explorer 有 bug

当 `active_window_id` 指向 sheet 窗口时，explorer 工具的 ingest 会错误定位到 sheet 窗口。

### 1.5 emoji 标记对 LLM 不友好

窗口渲染中大量使用 emoji（📁📊🎯📝⚠🧠📑📐📍🧊🧭📏🔗🎨），
在 tokenizer 中通常占 2-3 token，且不同模型对 emoji 语义理解不一致。

## 2. 设计方案：窗口类型策略模式

### 2.1 核心思路

引入 `WindowTypeStrategy` 协议，将 ingest、confirm、render 行为按窗口类型分发。
WindowState 数据结构不变，通过策略对象解耦行为。

### 2.2 策略协议

```python
class WindowTypeStrategy(Protocol):
    """窗口类型行为策略。"""

    def should_replace_result(self) -> bool:
        """unified 模式下是否用确认文本替代原始结果。
        返回 False 时走 enriched fallback（保留原始结果 + 追加感知块）。
        """
        ...

    def build_inline_confirmation(
        self,
        window: WindowState,
        tool_name: str,
        result_json: dict[str, Any] | None,
    ) -> str:
        """构建类型特定的 inline 确认文本。
        仅在 should_replace_result() 返回 True 时调用。
        """
        ...

    def apply_ingest(
        self,
        window: WindowState,
        tool_name: str,
        arguments: dict[str, Any],
        result_json: dict[str, Any] | None,
        iteration: int,
    ) -> None:
        """将工具结果摄入窗口数据容器。"""
        ...

    def render_full(
        self,
        window: WindowState,
        *,
        max_rows: int,
        current_iteration: int,
        intent_profile: dict[str, Any] | None,
    ) -> str:
        """渲染完整窗口内容（system_notice 中的 ACTIVE 级别）。"""
        ...

    def render_background(
        self,
        window: WindowState,
        *,
        intent_profile: dict[str, Any] | None,
    ) -> str:
        """渲染背景摘要。"""
        ...

    def render_minimized(
        self,
        window: WindowState,
        *,
        intent_profile: dict[str, Any] | None,
    ) -> str:
        """渲染最小化摘要。"""
        ...
```

### 2.3 ExplorerStrategy

```python
class ExplorerStrategy:
    """explorer 窗口策略。"""

    def should_replace_result(self) -> bool:
        return True  # 用 inline confirmation 替代原始 JSON

    def build_inline_confirmation(self, window, tool_name, result_json):
        """生成包含 entries 列表的 inline 确认。"""
        # 格式示例：
        # [OK] [explorer_1: .] list_directory | 12 items
        # [DIR] excelmanus
        # [DIR] tests
        # [XLS] 城市分组总金额汇总.xlsx (1.2MB, 2025-02-14)
        #   -- Sheet1: 1000r x 15c | header: [城市, 金额, 日期, ...]
        # [FILE] pyproject.toml (3.2KB)
        ...

    def apply_ingest(self, window, tool_name, arguments, result_json, iteration):
        """更新 explorer 窗口的 entries 和 total_rows。"""
        entries = extract_explorer_entries(result_json)
        window.metadata["entries"] = entries
        window.total_rows = len(entries)
        window.total_cols = 0  # explorer 无列概念
        ...

    def render_full(self, window, **kwargs):
        """渲染完整目录列表。"""
        # [explorer_1 -- 资源管理器]
        # [PATH] .
        # [DIR] excelmanus
        # [XLS] 城市分组总金额汇总.xlsx (1.2MB)
        # ...
        ...
```

对于 `scan_excel_files`，inline confirmation 更丰富：

```
[OK] [explorer_1: .] scan_excel_files | 3 excel files
[XLS] 城市分组总金额汇总.xlsx (1.2MB)
  -- Sheet1: 1000r x 15c | header: [城市, 金额, 日期, ...]
[XLS] 销售数据.xlsx (500KB)
  -- Sheet1: 200r x 8c | header: [产品, 数量, 单价, ...]
  -- Sheet2: 50r x 5c | header: [汇总, 总计, ...]
```

### 2.4 SheetStrategy

封装现有 `_apply_ingest` 中 sheet 分支、`render_window_wurm_full`、
`render_window_background`、`render_window_minimized` 的 sheet 逻辑。
行为不变，只是从 manager.py 中抽取到策略类。

### 2.5 策略注册与分发

```python
# window_perception/strategies.py

_STRATEGIES: dict[WindowType, WindowTypeStrategy] = {
    WindowType.EXPLORER: ExplorerStrategy(),
    WindowType.SHEET: SheetStrategy(),
}

def get_strategy(window_type: WindowType) -> WindowTypeStrategy:
    return _STRATEGIES[window_type]
```

manager.py 中的分发点：

```python
# ingest_and_confirm() 中
strategy = get_strategy(classification.window_type)
if not strategy.should_replace_result():
    return self._enriched_fallback(...)
# ... ingest + inline confirmation

# render_window_keep() 中
strategy = get_strategy(window.type)
return strategy.render_full(window, ...)
```

## 3. ASCII 标记替换 emoji

### 3.1 标记映射表

| 旧 emoji | 新标记 | 用途 |
|----------|--------|------|
| ✅ | `[OK]` | 工具执行成功 |
| ❌ | `[FAIL]` | 工具执行失败 |
| 📁 | `[DIR]` | 目录 |
| 📊 | `[XLS]` | Excel 文件 |
| 📄 | `[FILE]` | 普通文件 |
| 🎯 | `intent:` | 意图标签 |
| 📝 | `recent:` | 最近操作 |
| ⚠ | `[STALE]` | 数据过期警告 |
| 🧠 | `intent:` | 意图（合并到 intent:） |
| 📑 | `sheet:` | 当前工作表 |
| 📐 | `range:` | 数据范围 |
| 📍 | `viewport:` | 当前视口 |
| 🧊 | `freeze:` | 冻结窗格 |
| 🧭 | `scroll:` | 滚动条位置 |
| ↘️ | `remain:` | 剩余数据 |
| 📏 | `col-width:` | 列宽 |
| 🔗 | `merged:` | 合并单元格 |
| 🎨 | `style:` | 样式概要 |

### 3.2 窗口标题格式

旧：`【当前环境 · 资源管理器】`、`【后台 · 文件 / Sheet】`、`【挂起 · ...】`
新：`[ACTIVE -- 资源管理器]`、`[BG -- 文件 / Sheet]`、`[IDLE -- ...]`

### 3.3 确认协议格式

旧：`✅ [explorer_1: 未知文件 / 未知Sheet] list_directory: - | 0行×0列 | enriched | 意图=general`
新：`[OK] [explorer_1: .] list_directory | 12 items`（explorer inline confirmation）
新：`[OK] [sheet_1: file.xlsx / Sheet1] read_excel: A1:J25 | 100r x 10c | added@A1:J25 | intent=general`（sheet）

### 3.4 enriched 感知块格式

旧：
```
───────────── 环境感知 ─────────────
📊 文件: data.xlsx
🧠 意图: general
📑 当前Sheet: Sheet1
📐 数据范围: 100行 × 10列
📍 当前视口: A1:J25
────────────────────────────────────
```

新：
```
--- perception ---
file: data.xlsx
intent: general
sheet: Sheet1
range: 100r x 10c
viewport: A1:J25
--- end ---
```

## 4. 改动范围

### 4.1 新增文件

- `window_perception/strategies.py`：策略协议 + ExplorerStrategy + SheetStrategy

### 4.2 修改文件

| 文件 | 改动内容 |
|------|----------|
| `manager.py` | `ingest_and_confirm` 按策略分发；`_apply_ingest` explorer 分支委托策略；修复 `_resolve_target_window` explorer bug |
| `renderer.py` | `render_window_keep` / `render_window_background` / `render_window_minimized` 委托策略；`render_tool_perception_block` / `build_tool_perception_payload` emoji→ASCII；`render_system_notice` 标题格式 |
| `confirmation.py` | `serialize_confirmation` 中 `✅`→`[OK]`；explorer 确认格式 |
| `extractor.py` | `extract_explorer_entries` 中 emoji 前缀→ASCII 标记 |
| `rule_registry.py` | 无改动（分类逻辑不变） |
| `models.py` | 无改动（数据结构不变） |

### 4.3 测试

- 新增 `tests/test_window_strategies.py`：策略单元测试
- 修改现有窗口感知测试中的 emoji 断言→ASCII 断言

## 5. 分步实施计划

1. 新增 `strategies.py`，定义协议 + ExplorerStrategy + SheetStrategy
2. 修改 `extractor.py`：emoji→ASCII
3. 修改 `renderer.py`：emoji→ASCII + 委托策略渲染
4. 修改 `confirmation.py`：emoji→ASCII + explorer 确认格式
5. 修改 `manager.py`：ingest 按策略分发 + 修复 _resolve_target_window bug
6. 更新测试
7. 端到端验证：CLI 中 list_directory / scan_excel_files 在 unified 模式下返回正确内容

## 6. 风险与回退

- SheetStrategy 封装现有逻辑，行为不变，风险低
- ExplorerStrategy 是新行为，需要验证 inline confirmation 对不同 LLM 的效果
- ASCII 标记替换是纯文本变更，不影响逻辑，但需要更新所有相关测试断言
- 回退方案：如果策略模式引入问题，可以在 `get_strategy` 中返回 None 回退到原有逻辑
