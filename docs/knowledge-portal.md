# Agent 统一认知门户

入口为现有只读工具 `introspect_capability`。模型默认收到一句导航和查询 schema，需要时读取主题，不把整本文档常驻到提示词。适用于主会话以及有此工具权限的子代理；不需要启用 Agent 自我管理。

当前门户协议为 2，兼容原有目录、搜索、读取参数。文档查询不会访问外部服务。

## 与 OpenAI Docs MCP 的对应

2026-09-24 实际查询官方 `https://developers.openai.com/mcp` 的 `tools/list`，并调用搜索、浏览、锚点正文、端点目录与语言示例查询后，按以下分工设计。参考：[Docs MCP](https://developers.openai.com/learn/docs-mcp)。

| 官方能力 | ExcelManus 对应能力 |
|---|---|
| `search_openai_docs` | `knowledge_search`：命中段落、章节层级、准确引用及取正文调用 |
| `list_openai_docs` | `knowledge_index` 配合 `scope`、`limit` 与分页 |
| `fetch_openai_doc(url, anchor)` | `knowledge_read`：内部 ref、`#anchor`、行号范围与正文版本 |
| `list_api_endpoints` | `knowledge_index(scope="tools")` 或读取 `tools`：当前授权工具目录 |
| `get_openapi_spec` | `knowledge_spec`：实时函数输入/输出合同及示例；支持 `language` 和 `examples_only` |

额外提供章节目录、文档内查找和双向引用导航。这里的函数 schema 不是 HTTP OpenAPI；`citation` 使用内部 ref 与正文哈希，不虚构公开网页 URL，也不声称自动成为外部客户端的网页引用。官方网页引用要求可用 URL，见 [Citation behavior](https://developers.openai.com/api/docs/mcp#citation-behavior)。

```json
{"query_type":"knowledge_index"}
{"query_type":"knowledge_search","query":"如何读取 Excel 数据并安全修改"}
{"query_type":"knowledge_read","query":"doc:architecture"}
{"query_type":"knowledge_read","query":"runtime"}
{"query_type":"knowledge_read","query":"setting:max_iterations"}
{"query_type":"knowledge_read","query":"tool:apply_spreadsheet_changes.workbook_spec"}
{"query_type":"knowledge_index","scope":"docs","limit":5}
{"query_type":"knowledge_search","query":"如何处理版本冲突","scope":"docs"}
{"query_type":"knowledge_toc","query":"doc:execution"}
{"query_type":"knowledge_read","query":"doc:execution#sdk"}
{"query_type":"knowledge_find","ref":"doc:execution","query":"Workbook.save"}
{"query_type":"knowledge_related","query":"doc:execution"}
{"query_type":"knowledge_spec","query":"apply_spreadsheet_changes","language":"python","examples_only":true}
{"query_type":"knowledge_examples","query":"observe_spreadsheet"}
```

## 资源与事实源

| ref | 内容 | 事实源 |
|---|---|---|
| `doc:<id>` | 系统设计、工作流程、配置、执行边界、上下文、协作、恢复 | `excelmanus/knowledge/topics/*.md`，随产品打包 |
| `tools`、`tool:<name>[.<field>]`、`fields:<name>[.<field>]` | 当前工具目录、SDK/参数/输出详情、可继续读取的字段目录 | 当前调用有效目录与 ToolDef/schema；复用已有 introspection |
| `schema:<name>`、`schema-output:<name>` | 可分页重建的完整输入/输出 schema，保留所有分支和类型定义 | 当前 ToolDef 与输出合同 |
| `runtime` | 当前模型、视觉、模式、审批及宿主引擎探测 | 匹配的会话绑定、调用权限、runtime_capabilities |
| `settings`、`setting:<name>` | 公开配置实际值、schema、生效范围及修改条件 | self_management 共用的公开字段白名单 |
| `skills`、`skill:<name>` | 当前技能描述、状态及加载调用 | 当前会话 SkillpackLoader 与依赖检查 |
| `skill-text:<name>`、`resources:<name>`、`resource:<name>/<resource>` | 分页技能正文与声明的补充资源 | 同一 Loader 的 instructions/resource_contents，无任意路径读取 |
| `errors`、`error:<code>` | 错误分类与恢复建议 | 执行层 error_payload 词表 |
| `examples`、`example:<id>` | 已按当前 schema 校验的 JSON 步骤和 Python 代码 | `knowledge/examples.py` 与当前 ToolDef |
| `spec:<name>` / `knowledge_spec` | 完整函数规范及关联示例 | 当前输入、输出合同及示例库 |

`knowledge_search` 同时搜索文章正文、工具描述、完整输入 schema、公开设置、可调用技能正文与声明资源、错误及示例。使用本地段落 BM25、准确标识符加权和中英文术语别名，每个资源保留最相关段落；没有外部 embedding 或模型依赖。搜索结果只做导航，必须按 `next_call` 获取准确正文。权限不可用的技能正文与工具 schema 不进入索引。

`scope` 可选 all/docs/tools/settings/skills/errors/examples；`limit` 控制每页最大条数（1–20），返回内容另受页大小限制。空搜索返回该范围的导航入口；未命中提供建议而不判断能力不存在。搜索不会披露整个工具集合或激活技能。

文档解析保留标题层级、显式锚点、重复标题的独立标识，忽略代码块里的标题。`knowledge_toc` 列出可读取章节；`knowledge_find` 是大小写不敏感的字面查找，不执行用户正则；`knowledge_related` 提供出向引用、入向引用及章节上级。

`knowledge_read` 的工具详情会沿现有机制披露该授权工具；查询文档、目录和设置不会激活技能、改配置或扩大权限。技能条目给出 `load_call`，执行工作流前通过原有 skill 加载；正文和声明资源可沿门户链接分页读取，避免截断后只剩不可读的文件路径。技能读取同样检查可发现性、授权与依赖。

## 连通与版本

所有结果包含 `index_call`；资源链接带准确 ref 和 `next_call`。列表与长正文分页，沿顶层 `next_call` 继续。后续页面包含 `revision`，内容或目录改变时返回 `stale` 与 `restart_call`，禁止静默拼接不同版本。响应同时携带产品版本、门户协议版本和当前目录摘要。

搜索命中包含 `hierarchy`、`snippet`、`citation` 和版本绑定的正文调用。citation 的行号为 1-based，结束行包含在内；列号使用 Unicode 字符，`end_column_exclusive` 不包含结束字符。`content_revision` 是完整资源正文的哈希，独立于分页结果的 `revision`；搜索与读取之间内容发生变化也会明确拒绝旧引用。正文响应的 `citation.fetch_call` 可准确重取该页，保留语言和示例筛选条件。

调用示例不会执行工具。JSON 与 Python 代码从同一组步骤产生；`bindings` 标明真实观察值如何进入下一步，禁止拿占位版本当观察证据。示例参数用当前 schema 校验，授权缺失或 schema 漂移时返回 unavailable，不继续输出失效代码。未收录示例不表示功能不存在。

不存在的引用返回 `not_found`；当前无权限或未绑定会话返回 `unavailable`，并保留返回目录或相关说明的入口。源码隔离仍有效，门户不接受路径、URL，也不回退读取工作区中的同名文件。动态状态只使用与调用身份、会话和工作区匹配的引擎；子代理重新绑定自己的查询闭包。

## 维护与验证

新增文章须加入 `knowledge/documents.py` 的主题目录。文章中的内部引用使用 `[说明](knowledge:ref)`；不把仓库路径当作模型的下一跳。字段、错误和配置来自代码事实源，文章只维护用途、流程和边界。

工具详情使用实时 schema；不要重新引入脱离执行层的字段映射。公开设置同时供自我管理和门户使用，新增字段需进入显式白名单，禁止序列化整个 config 或模型档案。

`tests/test_knowledge_portal.py` 与 `tests/test_knowledge_docs_service.py` 覆盖目录到资源遍历、搜索排名与过滤、字段读取、章节和精确引用、双向关联、规范与示例、真实工具分发、Python SDK、配置隔离、子代理、源码隔离、分页及过期恢复。观察后修改示例还在临时工作簿上执行并核对真实单元格。包数据由 `pyproject.toml` 声明，桌面打包沿现有 `collect_all("excelmanus")` 收集。

这些测试证明查询路径和执行合同的连通性；模型是否在特定任务中主动选择查询仍需结合真实模型评测。
