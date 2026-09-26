# 表格与文档工作流程

## 定位与观察 {#observe}

读取或分析：用[文件目录](knowledge:tool:list_directory)定位目标，通过[工作簿观察](knowledge:tool:observe_spreadsheet)获得版本绑定事实。overview 定位工作表，range 读取有限区域；data、presentation、geometry、objects、dependencies 分别回答值、样式、尺寸、对象和依赖问题。区分未查询、空白、采样、不支持；全表结论必须补齐覆盖。

## 分析与公式重算 {#analysis}

聚合、筛选、质量检查使用[分析](knowledge:tool:analyze_spreadsheet)；多文件只读 SQL 使用[查询](knowledge:tool:query_spreadsheet)。公式文本与缓存值不同，缺缓存不等于空白。需要实际重算时先确认[运行环境](knowledge:runtime)，再用[计算](knowledge:tool:calculate_spreadsheet)，不把发现引擎等同于已计算成功。

## 新建与修改 {#edit}

创建或修改：确认目标和必要字段后使用[变更工具](knowledge:tool:apply_spreadsheet_changes)。新建通过 workbook_spec；已有文件修改基于同一次观察的 content_version/selection，提供要求的版本条件。具体操作字段按工具 schema 查询。版本冲突时重读并重新判断，不直接换上新版本重放旧计划。

工作区只有 CSV、尚无 xlsx 时[变更工具](knowledge:tool:apply_spreadsheet_changes)仍可用：新建工作簿不依赖已有 xlsx，直接用 workbook_spec 写出 outputs/ 下的交付物，不要用 openpyxl/pandas 直存。[转换](knowledge:tool:convert_spreadsheet)只用于真正的格式转换（xls/xlsb 等转 xlsx，或 data_only 仅迁移值），不是新建工作簿的前置步骤；它的产物工作表名是 input（不是 CSV 文件名），引用前先 observe_spreadsheet(overview) 确认。依赖已有工作簿的[公式追踪](knowledge:tool:trace_spreadsheet_formulas)在 CSV-only 下仍被目录门控，等 outputs/（或工作区顶层）出现 xlsx 后下一轮目录自动解锁。

## 外观与预览 {#appearance}

复刻或外观问题：同时取得 geometry 与 presentation；需要视觉判断时使用[预览](knowledge:tool:preview_spreadsheet)。图像也绑定工作簿版本，不能用旧图声称新文件已经符合要求。[渲染](knowledge:tool:render_spreadsheet)用于生成 PDF/PNG 产物，是否可用由具体引擎与工具结果决定。

图片收据、发票和表单还原使用[图片收据工作流](knowledge:workflow:receipt-visual-replica)：先读取图片并提取字段，再用 `purpose=visual_replica` 创建，随后按版本顺序重算、金额校验和预览。`run_code` 只用于有明确测量目标的补充探测，不把连续像素扫描当成主路线。

## 核验与交付 {#verify-deliver}

核验与交付：检查变更回执中的提交状态、文件与版本，再根据业务目标回读或使用[校验](knowledge:tool:validate_spreadsheet)、[对比](knowledge:tool:compare_spreadsheets)。已写入不自动等于业务正确。[打开工作簿](knowledge:tool:show_workbook)供用户查看，[下载](knowledge:tool:offer_download)供交付。需要用户指定范围时用 ask_user 的 selection；显示范围不构成授权。

预览默认 auto：请求范围与图表/图片相交时完整缩放该范围，避免图表被打印分页切开；显式 print 仍按真实打印设置分页。visual_coverage 区分完整与裁切对象，并提供裁切后的精确重试范围。自动预览不改变源文件打印布局，也不等于已核实实际 PDF 分页。

执行记录按实际提交绑定文件与版本。修改后旧校验和旧图像失效；业务校验失败或缺缓存不能由无关的成功调用清除。缺少必要证据时返回有界的核验提醒；无法完成时如实列出限制。SDK 调用中捕获异常不会自动登记为恢复，已提交文件也不会因父脚本失败而消失。

## Word 与其他文件 {#word}

Word 等非表格文件有独立工具及[技能目录](knowledge:skills)，是否提供取决于当前授权目录和工作区文件类型。未知工具或字段先查询，不把表格参数移植到 Word。

## 相关主题 {#next}

继续阅读：[代码执行](knowledge:doc:execution)、[错误恢复](knowledge:doc:recovery)、[版本工具](knowledge:tool:manage_spreadsheet_versions)、[新建示例](knowledge:example:create-workbook)、[观察后修改示例](knowledge:example:observe-edit)。
