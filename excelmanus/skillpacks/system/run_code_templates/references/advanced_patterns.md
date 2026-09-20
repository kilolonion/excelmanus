# 批量条件写入、跨表查找与能力边界

工作区 xlsx 的改写必须走 SDK。openpyxl 的内存修改不会自动变成 SDK operations。
调用参数和字段用 introspect_capability 的 tool_detail 查询，不去读产品源码猜实现。

## 批量条件写入

先 analyze_spreadsheet(mode="filter", conditions=[...], columns=[目标列])，
再 edit_spreadsheet(operations=[{kind:"write", selection:读结果.selection, values:二维矩阵}])。
矩阵行顺序对应 selection.rows，列顺序对应 selection.cols；不能按排序后的第几行当 Excel 行号。
完整可执行代码见 write_patterns.md。只有在确需 Python 计算时使用 run_code，简单条件和汇总直接调 native。

## 查找与分组

aggregate/pivot 的 join 是左连接，右表重复键取第一条；先 distinct(dup_only=true) 了解重复键，
需要一对多结果时在 run_code 里处理 SDK 返回的数据。两个工作表读取之间复用 expected_version，
防止混入不同版本；写回必须仍使用该观察版本。

分列用 edit.kind=transform action=split；清洗单列用 normalize_phone/normalize_date；
删除重复行用 dedupe。分列和去重不能维护复杂公式/对象引用时会拒绝；不要静默把公式变成值绕过。

## 文件损坏

打开失败先查看结构化 error_code/message，可能是格式不支持、损坏或权限问题，不能把所有错误都当作坏文件。
工作区复制使用 copy_file。修订恢复使用 manage_spreadsheet_versions，restore 必须显式 expected_version。

## VBA 与对象边界

能保留宏字节不等于执行宏。事件宏、ActiveX 和 UserForm 不属于当前能力。
当前 pivot 产生静态矩阵；原生 PivotTable、Table、命名范围、自动筛选、打印设置和图片插入没有公开修改入口。
图表目前可创建，不能更新/删除已有图表。不能用不落盘的 ws.add_table()/wb.save 片段声称完成。
