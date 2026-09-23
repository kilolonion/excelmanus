"""工具 description 与禁止出现在模型面的内部术语。

system 正文以 ``prompts/*.md`` 为唯一来源，经 PromptComposer 加载。
本模块不再保存 identity / persona / 工具策略正文副本。
"""

from __future__ import annotations

# ── 工具 description（是什么；跨调用习惯在对应 system 段）──

TOOL_DESCRIPTIONS: dict[str, str] = {
    "inspect_spreadsheet": (
        "只读探查。overview 看结构，range 读区域（可写 表!A1），"
        "search 跨表搜值，capabilities 查询本目录。"
        "返回 content_version；range 结果会提供可写回 selection 和原表列坐标。"
        "正文是投影预览，不是全表事实。"
    ),
    "analyze_spreadsheet": (
        "只读分析：profile/quality 看数据框全貌，filter 筛行，"
        "aggregate 分组汇总，pivot 二维透视，distinct 取值分布，"
        "relationships 看跨文件列关联，files 扫描目录。"
        "聚合与去重的 value 除 limit 外是全量。"
    ),
    "compare_spreadsheets": (
        "只读表格数据对比。必须提供两个工作簿，或同一工作簿中的两个不同工作表。"
        "有业务主键时用 alignment=key 并提供 key_columns；"
        "alignment=position 按行列，遇到增删行会对齐错。未指定 sheet 时只比较两边第一张表，"
        "ignore_style=false 时还比较单元格样式、合并、尺寸、打印设置、规则和富对象；"
        "返回 scope/compared_sheets/formula_status。"
    ),
    "trace_spreadsheet_formulas": (
        "只读公式分析：map 全景引用，trace 追踪先例/依赖，impact 看修改影响面。"
        "部分解析是限定证据，不是证明。"
    ),
    "edit_spreadsheet": (
        "原子编辑：写值或公式、插入或删除行列、表结构、透视写入、清洗变换，"
        "或传入 workbook_spec 创建新簿；workbooks 可把多个文件的 operations 放进同一原子事务。"
        "已有文件应带精确 content_version；"
        "省略时仅回退到本轮已观察版本，selection/restore 不允许省略。"
        "selection 可直接消费 inspect/analyze 的可写回句柄；copy 会复制样式并平移可维护的相对公式；"
        "pivot 覆盖非空目标表须 overwrite=true；去重/分列不能维护公式和对象时拒绝，单列规范化只改目标列；"
        "规格只用于创建；uploads/ 只读，改表写到 outputs/ 副本。"
    ),
    "format_spreadsheet": (
        "改外观：字体/填充/边框/对齐、合并、列宽、冻结窗格、打印布局(print_layout)、条件格式、数据验证。合并非空格需 allow_data_loss=true。"
        "整列写 B:B。一次 operations 提交。单表可省略 sheet；多表必须带 sheet 或 表!A1。"
    ),
    "split_spreadsheet": (
        "按某列取值把一个表拆成每组一个新 xlsx。只新建不覆盖："
        "任一目标已存在则全部取消；拆分键按原始值分组，安全文件名冲突会加后缀。"
        "公式/样式等不可复制对象会在 warnings 中列出。"
    ),
    "manage_spreadsheet_objects": (
        "富对象：图表创建/更新/删除，Table 创建/调整/删除，命名区域，图片、超链接、批注，以及可刷新原生透视表。"
        "对象操作一批提交；每个对象使用稳定 name/index/cell 定位，未指定的图表属性会保留。"
    ),
    "calculate_spreadsheet": (
        "显式调用可用的表格计算引擎重算公式，扫描错误单元格后原子发布。"
        "引擎不可用、公式错误或格式不支持时不写入，并返回明确状态。"
    ),
    "render_spreadsheet": (
        "将工作表或打印区域渲染为 PDF 或分页 PNG，返回页数、引擎、源版本和实际产物。"
        "需要宿主 soffice；PNG 还需要 Poppler。"
    ),
    "convert_spreadsheet": (
        "将 xls/xlsb 等工作簿转换为 xlsx，保留源文件并返回转换前后对象清单及损失警告。"
        "preserve 使用表格引擎，data_only 明确只迁移单元格值。"
    ),
    "validate_spreadsheet": (
        "按规则确定性校验工作簿：unique、required、foreign_key、row_expression、total、formula_errors。"
        "返回检查数量、失败单元格和截断状态，不修改文件。"
    ),
    "query_spreadsheet": (
        "将多个 Excel/CSV 源逐行导入临时磁盘 SQLite，执行只读 SELECT/WITH 查询。"
        "返回有限预览，可将完整结果原子导出为 xlsx/csv；保留源版本依赖。"
    ),
    "manage_spreadsheet_versions": (
        "list 当前版本与检查点（只读）；checkpoint 打快照；restore 按 revision 恢复；delete 删除显式检查点。"
        "历史读取结果不可当作写入目标。"
    ),
    "skill": "按会话技能目录中的准确名称，加载该技能的完整说明。",
    "delegate": (
        "把自包含任务交给子代理，省略 agent_name 用通用 subagent；它不继承主对话。"
        "默认等待终态；background=true 返回 run_id，主代理可继续工作。"
        "action=status/list/wait 查询运行与结果，send 追加指令，pause/cancel 停止，resume 携带历史继续并返回新 ID。"
        "后台返回值为 {status, run}，任务终态及输出在 run.status/run.result；启动成功不代表任务完成。"
        "tasks 用于同步并行独立探查。"
    ),
    "list_subagents": "列出当前可委托的子代理名称与职责。",
    "ask_user": (
        "当实质歧义挡住安全完成时，向用户提一个结构化问题。不要问探查能发现的事实。"
        "无应答通道时按最合理默认推进并在回复里声明口径。"
        "需要用户指定表格范围时，在问题中提供 selection（file_path、sheet、可选 ranges 建议范围）；"
        "侧栏会打开让用户选择并确认，返回版本绑定的 selection。文字补充不等于选区确认。"
    ),
    "show_workbook": (
        "打开用户的表格侧栏并临时高亮 target.ranges。stage=inspect 定位查看，planned 展示准备修改的区域，"
        "changed 展示已修改的区域。changed 必须在写入成功后调用并携带写入回执的 content_version；"
        "范围是你标记的范围，应依据实际写入结果，不得把计划声称为完成。此工具不修改文件、不代表用户批准。"
        "需要用户确认范围时使用 ask_user.selection。"
    ),
    "write_plan": "把当前计划写成工作区文档。",
    "exit_plan_mode": (
        "呈交完整计划供用户批准后退出计划模式。"
        "仅在计划模式内有效；必须是该次回复的唯一或最后一次工具调用。"
    ),
    "run_code": (
        "用 Python 编排循环、跨工具组合或自定义计算；单次业务操作优先直接调用工具。"
        "import em 可调用当前授权目录中的同名函数；未知参数或返回字段先查询 introspect_capability 的 tool_detail，再编写程序。"
        "仅在审批策略为「跳过」时允许网络访问和子进程。"
        "只输出需要模型判断的结果。"
    ),
    "read_image": (
        "把工作区 PNG/JPEG/WebP/GIF 或会话 attachment_id 加载到当前视觉上下文。"
        "不要做 OCR，不要另开视觉模型，不要为看图先写缩略图。"
    ),
    "offer_download": "为用户可下载的产出生成下载。探测文件或临时脚本不要调用。",
}

FORBIDDEN_MODEL_TERMS: tuple[str, ...] = (
    "SSE",
    "ToolCallCard",
    "Univer",
    "workbook_commit",
    "ContextBuilder",
    "engine.py",
    "ui_meta",
    "canonical JSON",
    "Host",
    "Runtime",
    "Client Remote",
    "pipeline_stage",
    "fingerprint",
    "AST GREEN",
    "AST YELLOW",
    "AST RED",
)
