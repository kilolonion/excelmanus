# Workbook V2 修复与验证记录

日期：2026-09-24。检查对象是当前协作工作树，不是 HEAD，也不是已发布安装包。本轮未提交或覆盖其他任务的改动。

## 修复范围

| 环节 | 当前实现与验证 |
|---|---|
| 统一观察 | Agent、HTTP、历史预览经 WorkbookService；已固定的历史快照不重新读活路径。搜索按同一快照分页；CSV 严格区分未请求/不支持。 |
| 覆盖与稀疏读取 | 多区域/整轴有界读取；unloaded 保留区域间的孔洞；查询不再创建空白单元格污染缓存；总览返回副本。 |
| 公式与显示 | 缺失缓存不作为空白/零进入数值分析；数组公式可以 JSON 序列化，保留公式类型和范围。保存缓存、原始值、公式、计算来源分开。未计算单元格显示明确占位，窗口刷新不重算屏外引用。 |
| 几何与编辑 | 原生尺寸和估算像素分开；UI 发送 geometry.resize，由后端换算。geometry.scale 报告整轴影响、区域外单元格/对象锚点；preserve_outside 可拒绝冲突。小比例不会被像素取整反向执行。 |
| 事务证据 | dry_run 使用相同编译/序列化路径；连续尺寸操作按最终合成结果验证；保留原始请求和实际差值。批次错误保留全局 operation_index；未发布目标不被标为落盘证据。 |
| 输入与保真 | style 不得夹带 kind/sheet/range；源图片和跨文件 join 绑定读取版本。OOXML 未知部件、签名、不支持扩展拒绝修改；核对关系图、未改图片和 VBA。当前允许并回填已识别的 LibreOffice ExcelA1 计算扩展及空自定义属性部件，其他扩展仍拒绝。 |
| 对象 | WorkbookSpec 图表/图片编译为同一 ChangeSet；删除独立 create_excel_chart 提交通道。修复最后一条批注/超链接删除、已加载图片/图表尺寸未写入绘图锚点的问题。 |
| 预览 | 工作台/打印明确区分。提供渲染器版本、字体环境指纹、源/附件尺寸、坐标转换和显示文本。资源哈希清单检查缺失/过期产物；工作台缓存有数量、容量和时效边界。 |
| UI | 跨窗口合并锚点、隐藏轴、日期数值映射、覆盖判断共用 V2。样式是否已加载按区域判断，其他窗口的数据预取不会阻止当前区域编辑。未显示对象/条件格式有用户提示。 |
| 构建与发现 | Python/SDK/tool_detail 共用 schema；生成 TypeScript wire 类型并提供漂移检查。wheel 构建检查渲染资源；桌面构建准备内置 Chromium，frozen smoke 实际调用 preview。移除无消费者的 pytest-qt 依赖，默认 pytest 可运行。 |

## 实测结果

- 工作簿扩展验收：623 项通过（修复过程中一次较宽的 Python 测试集合）。
- 最终可重复入口：`uv run python -m scripts.check_workbook_v2`，**337 项 Python 测试通过，7 个前端专项文件、61 项测试通过**。包含真实 headless 工作台预览、Native/SDK/HTTP 版本冲突、历史、对象、保真与前端专项。
- 前端全套：128 个测试文件、923 项通过。
- `npm --prefix web run build`：通过，包含 TypeScript 检查。
- `scripts/generate_workbook_contracts.py --check`、Python 编译、针对新模块的 Ruff F821/F811/F401、`git diff --check`：通过。
- 浏览器：`web/scripts/check-workbook-loading.mjs` 的 23 个场景通过；37 次观察、4 次写请求。测试使用真实 React/Univer 和确定性 HTTP 夹具，不调用模型。覆盖本地编辑、连续写入、远端删除、跨表/跨文件、隐藏刷新、冲突保留/重载、选区、加载期间禁止编辑等。
- 独立 wheel：使用 `uv build --wheel` 构建，在源码目录外安装并以 `python -I` 执行；逐文件校验安装后的核心源码哈希与本次工作树一致。workbench 生成 128×40 图，Chromium 153.0.8010.12；print 生成 794×1123 图，LibreOffice 26.2.1.2。没有修改源工作簿。

全仓 Python 最近一次完整运行：6353 passed、7 failed、13 skipped。随后修复了其中 2 项（HTTP 测试请求缺 headers；旧 persona 文案断言），相关 57 项重跑通过。其余 5 项在独立重跑中仍失败，属于并行的非工作簿改动，不能声称全仓全绿：

| 文件 | 剩余失败 |
|---|---|
| `tests/test_events.py` | 3 项，事件/字段快照未包含 dispatch 等新增事件和字段 |
| `tests/test_model_profile_sync.py` | 1 项，OAuth 自动模型配置的期望模型与实际配置不一致 |
| `tests/test_sse_e2e.py` | 1 项，订阅流缺少测试预期的第三条实时消息，后台任务还有 mock 序列化错误 |

## 重跑方式

```bash
uv sync --extra web
npm --prefix web ci
uv run python -m playwright install chromium
npm --prefix web run build:preview
uv run python -m scripts.generate_workbook_contracts --check
uv run python -m scripts.check_workbook_v2
npm --prefix web run build
uv build --wheel
```

浏览器检查还需 Node `playwright` 包，可在独立目录安装后用 NODE_PATH 指向其 node_modules，再运行：

```bash
uv run python -m scripts.check_workbook_v2 --browser
```

生成合同发生变更时先运行 `uv run python -m scripts.generate_workbook_contracts`；显示模块变更后重新 `npm --prefix web run build:preview`。不要只复制旧的 workbench.js。

## 仍需发布验收的边界

- 尚未执行真实模型“自然语言 → 选证据 → 选比例 → 看图修正”评测；确定性测试不代表自然语言理解或图片复刻质量已达标。
- 尚未执行完整 Windows/macOS 安装包、签名、跨系统字体与首次安装验收；独立 wheel 成功不等于正式桌面包通过。
- 当前字符宽度使用显式 7px 估算；不声称测得 Excel 字形指标或最低可读高度。Univer core 不渲染图片/图表/条件格式。print 可能由 LibreOffice 重算，不能当作原始缓存的工作台图。
- 两点锚定绘图的尺寸修改需要显式 target_cell，转为固定尺寸锚点；不支持的转换明确拒绝。数组/特殊公式的原生重算仍由专用计算引擎负责。
- 64 MiB XML 解析预算和有界缓存是当前资源边界，不承诺任意百万行复杂格式文件低内存。尚未完成百万行、字体长尾、全部 OOXML 扩展性能/保真基准。
- 渲染支持是明确的子集；未知扩展不能通过更换输出路径绕过保真检查。

本轮修复说明见 [架构实施说明](workbook-perception-architecture-v2.md)。
