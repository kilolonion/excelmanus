"""Task-level capability discovery from executable, version-bound recipes."""
from __future__ import annotations

from excelmanus.knowledge.examples import Example, example_detail

WORKFLOWS = {
    "spreadsheet-report": {
        "title": "汇总、同比与图表看板", "keywords": ("sales", "dashboard", "report", "看板", "销售", "同比", "报告", "图表"),
        "example": Example("report-workflow", "创建、重算、校验和图表预览", "完整工作簿闭环；示例数字需替换为实际分析结果。",
                           ("apply_spreadsheet_changes", "calculate_spreadsheet", "validate_spreadsheet", "preview_spreadsheet")),
        "guidance": [
            "先观察源文件，再按最细业务粒度一次聚合并复用结果；同比保留年份，月份按数值排序。不要重复 aggregate/pivot 同一口径。",
            "用数值结果生成结论；事实、假设和建议分开。只有两年数据不能推断两年各自同比增长。",
            "创建前安排图表边界和打印区域，合并区域的非锚点留空。图表 sheet 是数据源，target_sheet 是放置表。",
            "每一步绑定上一步返回版本；重算/修改与针对新版本的校验不能并行。微调已有文件，不删除重建。",
            "preview_spreadsheet 默认自动选择能显示图表的渲染方式；核对 visual_coverage，不用导出文件替代观察。",
        ],
    },
    "receipt-visual-replica": {
        "title": "图片收据还原为可编辑工作簿",
        "keywords": ("receipt", "收据", "收款收据", "图片还原", "版式还原", "视觉复刻", "visual replica", "金额核对"),
        "example": Example("receipt-visual-replica", "图片收据还原完整闭环", "创建、重算、金额校验和版式预览。",
                           ("apply_spreadsheet_changes", "calculate_spreadsheet", "validate_spreadsheet", "preview_spreadsheet")),
        "guidance": [
            "可以用 read_image 读取原图，再整理客户、日期、明细、数量、单价、金额和合计，结合这些事实决定表格行列。",
            "图片还原通常传 purpose=visual_replica，并提供完整 row/column geometry 或 layout_reference；像素坐标可按工具单位换算。",
            "金额列可以写成数量×单价公式，合计可以使用 SUM；日期可以采用 ISO 字符串或 schema 接受的值。",
            "常见核验顺序是按 content_version 依次 calculate → validate(cell/total/formula_errors) → preview；也可以根据工具结果调整顺序。",
            "geometry 候选、read_image crop、run_code 自定义测量和 uncertainties 都可以作为版式判断依据，按新增证据决定是否继续探测或提交草稿。",
        ],
    },
    "cross-workbook-automation": {
        "title": "跨表匹配、公式写回与异常标记",
        "keywords": ("cross workbook", "跨表", "订单", "产品目录", "匹配产品", "未匹配", "公式写回"),
        "example": Example("cross-workbook-automation", "跨表匹配与公式写回", "观察、分析、写回、重算和校验。",
                           ("observe_spreadsheet", "analyze_spreadsheet", "apply_spreadsheet_changes", "calculate_spreadsheet", "validate_spreadsheet")),
        "guidance": [
            "可以先观察工作簿和工作表，再确定匹配键、输出列、未匹配标记方式和版本依赖。",
            "analyze_spreadsheet 的 join/relationships 可用于只读核对；apply_spreadsheet_changes 可用于实际写回，二者共享同一事实版本。",
            "匹配规则可以是精确、规范化或自定义计算，按用户目标和数据质量选择；不确定的匹配保留待核对状态。",
            "常见后续是 calculate_spreadsheet 与 validate_spreadsheet(formula_errors/cell/required)，需要版式证据时再 preview。",
        ],
    },
    "statistical-analysis": {
        "title": "统计分析、回归图表与预测写回",
        "keywords": ("regression", "回归", "统计分析", "散点", "预测公式", "广告投入", "销售增长", "correlation"),
        "example": Example("statistical-analysis", "统计分析与回归写回", "计算、建表、重算、校验和预览。",
                           ("run_code", "apply_spreadsheet_changes", "calculate_spreadsheet", "validate_spreadsheet", "preview_spreadsheet")),
        "guidance": [
            "run_code 可以承担回归或统计计算，先从已观察数据确认自变量、因变量、缺失值处理和模型口径。",
            "apply_spreadsheet_changes 可以创建结果工作簿、公式区域和原生图表；CSV 工作区也可以直接创建第一个 xlsx。",
            "预测公式的位置、预测范围和图表类型可以依据用户目标与数据结构确定，并在 uncertainties 中记录未指定口径。",
            "结果可以按 content_version 继续 calculate、validate(formula_errors/cell) 和 preview，分别核对数值、公式和视觉对象。",
        ],
    },
}


def _workflow_matches(query: str, key: str, item: dict) -> bool:
    query = query.strip().lower()
    if not query:
        return False
    if query == key.lower():
        return True
    terms = item.get("keywords", ())
    if isinstance(terms, str):
        terms = tuple(terms.split())
    # English aliases use whole words; Chinese terms are explicit phrases.
    import re
    for term in terms:
        value = str(term).strip().lower()
        if not value:
            continue
        if value.isascii():
            if re.search(rf"(?<![a-z0-9_]){re.escape(value)}(?![a-z0-9_])", query):
                return True
        elif value in query:
            return True
    return False


def workflow_detail(query, source, language="all"):
    query = query.strip()
    matches = [key for key, item in WORKFLOWS.items() if _workflow_matches(query, key, item)]
    if len(matches) != 1:
        return {"status": "not_found" if query else "ok", "items": [
            {"id": key, "title": item["title"], "next_call": {"name": "introspect_capability", "arguments": {"query_type": "knowledge_workflow", "query": key}}}
            for key, item in WORKFLOWS.items()]}
    key = matches[0]
    definition = WORKFLOWS[key]
    example = example_detail(definition["example"], source, "python" if language == "all" else language)
    if example.get("status") == "unavailable":
        return example
    from excelmanus.tools.introspection_tools import _record_loaded_tool
    for name in definition["example"].tools:
        _record_loaded_tool(source[name])
    steps = [{"id": step["id"], "tool": step["call"]["name"], "bindings": step.get("bindings", {})} for step in example.pop("steps")]
    return {"workflow": key, "guidance": definition["guidance"], "steps": steps, "example": example,
            "contracts": {name: {"description": source[name].description,
                         "detail_call": {"name": "introspect_capability", "arguments": {"query_type": "tool_detail", "query": name}}}
                         for name in definition["example"].tools}}
