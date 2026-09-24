# 文档服务与查询方法

## 搜索后读取准确正文 {#search-fetch}

不清楚产品能力、流程、配置或限制时，先用 introspect_capability 的 knowledge_search 搜索；命中只用于定位，按返回的 next_call 读取准确正文后再作判断。搜索覆盖随包文档、当前工具与输入 schema、公开配置、可加载技能正文和声明资源、错误说明及示例。使用 scope 限定 docs/tools/settings/skills/errors/examples；不确定关键词时用 knowledge_index 浏览。

检索是本地 BM25 段落排序，加上准确标识符匹配和中英文术语别名。它不调用外部搜索、向量服务或另一个模型。搜索未命中不证明功能不存在，命中也不证明当前可以执行。工具授权和运行依赖分别查询[当前工具](knowledge:tools)与[运行状态](knowledge:runtime)。

## 章节、行号与文档内查找 {#sections}

knowledge_toc 返回标题层级、章节锚点和读取调用。knowledge_read 的 query 可写 doc:execution#sdk，只取指定章节；line_start/line_end 可进一步缩小到原文中的 1-based 行号。knowledge_find 的 ref 指定文档或章节，query 填要查找的字面文本。结果位置来自原文，不把工具搜索摘要作为原文引用。

例如先查[执行说明](knowledge:doc:execution)，再读取[Python SDK 章节](knowledge:doc:execution#sdk)。每个读取结果带正文哈希和行列位置；分页按 next_call 继续，遇到 stale 按 restart_call 重新获取。内容变动后不能继续拼接或引用旧分页。

## 工具规范与示例 {#spec-examples}

知识目录中的 tools 对应当前可调用工具清单。knowledge_spec 的 query 填准确工具名，返回完整输入 schema、输出合同、写效应与适用示例。language 可选 python/json/all；examples_only=true 时只返回示例。这里是函数工具合同，不把它称为 HTTP OpenAPI，也不假定出现于产品文档的工具已获授权。

knowledge_examples 可按工具名或关键词找到示例，随后读取 example:引用。示例的 JSON 调用步骤和 Python em 代码来自同一份维护内容，并按当前 schema 校验。路径、表名和业务值需要替换；版本绑定必须来自前一步真实结果。获取示例不执行工具，schema 校验通过也不等于业务执行成功。

可查询[创建示例](knowledge:example:create-workbook)、[观察后修改](knowledge:example:observe-edit)与[调用示例目录](knowledge:examples)。如果当前模式缺少所需工具或示例不符合当前 schema，会明确返回 unavailable，不提供失效调用。

## 关联与证据 {#citations}

knowledge_related 返回当前文档引用的资源、引用它的文档，以及章节的上级页面。文档设计、操作步骤、工具参数、运行条件和恢复说明应沿这些引用衔接，不要跳到无法读取的仓库路径。

citation 是可重新获取的内部证据：ref、content_revision、行号和列号。正文响应还给出 fetch_call，用原调用和版本重取该段。它不是公开网页 URL，不假装具有外部客户端的网页引用能力。密钥、连接地址和未授权资源不会进入正文或搜索索引。

继续阅读：[系统设计](knowledge:doc:architecture)、[工作流程](knowledge:doc:workflows)、[当前配置](knowledge:settings)、[错误恢复](knowledge:doc:recovery)。
