"""工具 description 与禁止出现在模型面的内部术语。

system 正文以 ``prompts/*.md`` 为唯一来源，经 PromptComposer 加载。
本模块不再保存 identity / persona / 工具策略正文副本。
"""

from __future__ import annotations

# ── 工具 description（是什么；跨调用习惯在对应 system 段）──

TOOL_DESCRIPTIONS: dict[str, str] = {'observe_spreadsheet': '读取同一版本的工作簿事实。overview 总览，range '
                        '定点观察；facets=data/presentation/geometry/objects/dependencies 可组合。geometry '
                        '对 xlsx 包含有效尺寸、隐藏状态和宽高比例；CSV 无几何信息（geometry 返回 unsupported）。'
                        'CSV 无存储类型，单元格带 inferred_type(number/text/date) 并按列给出 '
                        'type_summary（header/numeric_ratio/空值计数）。空与未查询有不同 coverage。',
 'analyze_spreadsheet': '分析版本绑定的数据：profile/quality/filter/aggregate/distinct/pivot/relationships/files。版式观察用 '
                        'observe_spreadsheet。',
 'compare_spreadsheets': '比较两个工作簿或工作表；ignore_style=false 同时对比样式、尺寸、合并、规则和对象 XML。像素相似需要独立图像观察。',
 'trace_spreadsheet_formulas': '追踪公式先例、依赖和修改影响，报告覆盖与不支持项。',
 'apply_spreadsheet_changes': '一次事务完成新建、值、公式、格式、合并、列宽行高、对象和打印设置。operations 按 kind 查字段。geometry.scale 的 x '
                              '控制横向、y 控制纵向；size 使用 column_widths(字符) 与 '
                              'row_heights(pt)。返回最终版本和实际几何变动，未看图不声称视觉完成。',
 'split_spreadsheet': '按列分组生成多个工作簿，保留原始文件并报告对象复制边界。',
 'calculate_spreadsheet': '显式调用可用的表格计算引擎重算公式，扫描错误单元格后原子发布。引擎不可用、公式错误或格式不支持时不写入，并返回明确状态。',
 'render_spreadsheet': '将工作表或打印区域渲染为 PDF 或分页 PNG，返回页数、引擎、源版本和实际产物。需要宿主 soffice；PNG 还需要 Poppler。',
 'convert_spreadsheet': '将 Excel 工作簿转换为 xlsx、CSV、HTML、ODS、PDF 或 PNG，保留源文件并返回版本、引擎和图片/图表对象损失警告。preserve 使用表格引擎，data_only 明确只迁移单元格值。',
 'validate_spreadsheet': (
     '确定性业务校验：unique/required/foreign_key/row_expression/total/cell/formula_errors；'
     'formula_errors 未指定 sheet 时检查全部工作表，其他规则在多表工作簿中须指定 sheet；'
     '返回失败单元格与真实覆盖数量，不修改文件。公式缓存缺失时默认返回 validation_status=partial、'
     'valid=null 和未计算单元格；allow_uncached=false 才阻断。列引用接受列名、列字母或 1-based 列号。'
     'row_expression 作用于当前行的列，不支持 A1 单元格引用（B7 是列名不是单元格）；'
     'total 是整列求和（空值跳过、非数值文本单独计入 excluded_non_numeric，不混入 actual/expected）；'
     '断言单个单元格请用 cell 规则：sheet + cell（A1 单格，如 B7）+ expected，数值按 tolerance'
     '（默认 1e-9）、文本精确比对（case_sensitive 默认 true）、expected=null 断言空值，'
     '失败项 {rule,kind,sheet,cell,expected,actual,delta?} 只放机器可读值，也可用 observe_spreadsheet 回读。'
 ),
 'query_spreadsheet': '将多个 Excel/CSV 源逐行导入临时磁盘 SQLite，执行只读 SELECT/WITH 查询。返回有限预览，可将完整结果原子导出为 '
                      'xlsx/csv；保留源版本依赖。',
 'manage_spreadsheet_versions': '列出、创建或删除检查点，按版本恢复工作簿。恢复必须提供 expected_version。',
 'skill': '按会话技能目录中的准确名称，加载该技能的完整说明。',
 'delegate': '把自包含任务交给子代理，省略 agent_name 用通用 subagent；它不继承主对话。默认等待终态；background=true 返回 '
             'run_id，主代理可继续工作。action=status/list/wait 查询运行与结果，send 追加指令，pause/cancel 停止，resume 携带历史继续并返回新 '
             'ID。后台返回值为 {status, run}，任务终态及输出在 run.status/run.result；启动成功不代表任务完成。tasks 用于同步并行独立探查。',
 'list_subagents': '列出当前可委托的子代理名称与职责。',
 'ask_user': '当实质歧义挡住安全完成时，向用户提一个结构化问题。不要问探查能发现的事实。无应答通道时按最合理默认推进并在回复里声明口径。需要用户指定表格范围时，在问题中提供 '
             'selection（file_path、sheet、可选 ranges 建议范围）；侧栏会打开让用户选择并确认，返回版本绑定的 selection。文字补充不等于选区确认。',
 'show_workbook': '打开用户的表格侧栏并临时高亮 target.ranges。stage=inspect 定位查看，planned 展示准备修改的区域，changed '
                  '展示已修改的区域。changed 必须在写入成功后调用并携带写入回执的 '
                  'content_version；范围是你标记的范围，应依据实际写入结果，不得把计划声称为完成。此工具不修改文件、不代表用户批准。需要用户确认范围时使用 '
                  'ask_user.selection。',
 'write_plan': '把当前计划写成工作区文档。',
 'exit_plan_mode': '呈交完整计划供用户批准后退出计划模式。仅在计划模式内有效；必须是该次回复的唯一或最后一次工具调用。',
 'run_code': '用 Python 编排循环、跨工具组合或自定义计算；单次业务操作优先直接调用工具。import em 可调用当前授权目录中的同名函数；未知参数或返回字段先查询 '
             'introspect_capability 的 tool_detail，再编写程序。仅在审批策略为「跳过」时允许网络访问和子进程。只输出需要模型判断的结果。',
 'read_image': '把工作区 PNG/JPEG/WebP/GIF 或会话 attachment_id 加载到当前视觉上下文。一次任务中同一附件同一 detail/crop 不要重复调用；需要看清小字时用 crop={x,y,width,height,zoom} 一次裁剪并放大（zoom 1–8），需要不同区域才再次读取。视觉复刻前可传 analyze_layout=true，一次性返回有界的行列线候选、墨迹边界和可转成 layout_reference 的候选；布局摘要是提示性结果，可结合原图判断。',
 'convert_image': '将工作区图片转换为 PNG/JPEG/WebP/GIF/BMP，可按尺寸和适配方式缩放，并原子写出到新路径；返回源版本、输出版本和尺寸。',
 'offer_download': '为用户可下载的产出生成下载。探测文件或临时脚本不要调用。',
 'preview_spreadsheet': '观察指定工作表区域的图像与几何，图像直接进入视觉上下文。workbench 与当前工作台共用渲染模块；print 为 LibreOffice '
                        '打印页。仅写派生缓存，不改工作区文件，read/plan 可用。'}

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
