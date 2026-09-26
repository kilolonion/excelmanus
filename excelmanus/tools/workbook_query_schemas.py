"""Canonical V2 query schemas; field names are shared with workbook observation."""
from copy import deepcopy
QUERY_SCHEMAS = {'analyze_spreadsheet': {'additionalProperties': False,
                         'properties': {'aggfunc': {'description': 'pivot 聚合函数，默认 sum', 'type': 'string'},
                                        'aggregations': {'description': 'aggregate 的聚合规格：{列名: 函数或函数数组} 对象、'
                                                                        '[{column, func}] 数组，或等价 JSON 字符串（自动解析）；函数 '
                                                                        'sum/count/mean/min/max/median/std/nunique/first/last；"*" '
                                                                        '表示行计数；TopN 效果用 sort_by+max_rows（ascending=false 取最大，不要传 limit）不是函数。示例：'
                                                                        '{"销售额(万元)": ["sum", "mean", "max", "min"]}',
                                                         'type': ['object', 'array', 'string']},
                                        'ascending': {'default': True,
                                                      'description': 'filter/aggregate 默认升序；最大 TopN 显式传 '
                                                                     'false（降序）并设置 max_rows。',
                                                      'type': 'boolean'},
                                        'column': {'description': 'filter/distinct 的目标列；aggregate/pivot '
                                                                  '单条件筛选用 column+operator+value',
                                                   'type': 'string'},
                                        'columns': {'description': 'pivot 的列维度：列名或列名数组，也支持日期派生键对象 {column, '
                                                                   'transform}（如按月份分列传 '
                                                                   '{"column":"日期","transform":"year_month"}）；filter '
                                                                   '下为选中列名数组；接受列名、数组/对象或等价 JSON 字符串（自动解析）。'
                                                                   '示例：["年份"]',
                                                    'type': ['string', 'array', 'object']},
                                        'conditions': {'description': '[{column,operator,value}] 或等价 JSON 字符串（自动解析）；'
                                                                      'isnull/notnull '
                                                                      '不需要 value；between 用 [下界,上界]，in/not_in '
                                                                      '用数组；conditions=[] 表示不过滤。示例：'
                                                                      '[{"column":"年份","operator":"eq","value":2023}]',
                                                       'items': {'additionalProperties': False,
                                                                 'properties': {'col': {'description': 'column '
                                                                                                       '别名',
                                                                                        'type': 'string'},
                                                                                'column': {'type': 'string'},
                                                                                'op': {'description': 'operator '
                                                                                                      '别名',
                                                                                       'type': 'string'},
                                                                                'operator': {'type': 'string'},
                                                                                'value': {}},
                                                                 'type': 'object'},
                                                       'type': ['array', 'object', 'string']},
                                        'directory': {'description': 'files/relationships 的扫描目录，默认工作区根',
                                                      'type': 'string'},
                                        'dup_only': {'description': 'distinct 只看重复取值（count>1）并附行号',
                                                     'type': 'boolean'},
                                        'expected_version': {'description': '可选；要求分析基于这次读取到的 '
                                                                            'content_version。',
                                                             'type': 'string'},
                                        'file_path': {'type': 'string'},
                                        'file_paths': {'description': 'relationships 的目标文件列表；单文件直接用 '
                                                                      'file_path；接受字符串数组或等价 JSON 字符串（自动解析）。'
                                                                      '示例：["a.xlsx","b.xlsx"]',
                                                       'items': {'type': 'string'},
                                                       'type': ['array', 'string']},
                                        'group_by': {'description': 'aggregate 的分组列名或列名数组（pivot 中作 index '
                                                                    '别名）；也支持日期派生键对象 {column, '
                                                                    'transform}，transform=year|quarter|month|year_month|date|week|hour（按月份聚合传 '
                                                                    '{"column":"日期","transform":"year_month"}）；不传则整体汇总；'
                                                                    '接受列名、数组/对象或等价 JSON 字符串（自动解析）。示例：["区域", "年份"]',
                                                     'type': ['string', 'array', 'object']},
                                        'header_row': {'description': '列头所在行号（Excel 行号，1-based，第 1 行 = '
                                                                      '1），默认自动检测；表单类文档传 -1',
                                                       'type': 'integer'},
                                        'include': {'description': 'files 模式附加维度；接受字符串数组或等价 JSON '
                                                                   '字符串（自动解析）',
                                                    'items': {'type': 'string'},
                                                    'type': ['array', 'string']},
                                        'index': {'description': 'pivot 行维度：列名或列名数组，也支持日期派生键对象 {column, '
                                                                 'transform}（同 group_by）；接受列名、数组/对象或等价 '
                                                                 'JSON 字符串（自动解析）。示例：["区域"]',
                                                  'type': ['string', 'array', 'object']},
                                        'join': {'additionalProperties': False,
                                                 'description': 'aggregate/pivot '
                                                                '跨表连接；支持单列或等长度多列键、left/inner/right/outer/left_anti/right_anti。left '
                                                                '连接保持 VLOOKUP 首匹配语义，其余连接保留键重复。连接列可参与 '
                                                                'group_by/aggregations/conditions。接受结构化对象或等价 JSON '
                                                                '字符串（自动解析）。示例：{"sheet":"产品","on":"产品ID","columns":["名称"]}',
                                                 'properties': {'columns': {'description': '从右表带来的列；缺省为右表全部非键列',
                                                                            'items': {'type': 'string'},
                                                                            'type': ['array', 'string']},
                                                                'expected_version': {'description': '可选的右工作簿版本；同簿只读连接自动使用左侧观察版本。',
                                                                                     'type': 'string'},
                                                                'file_path': {'description': '另一文件的工作区相对路径',
                                                                              'type': 'string'},
                                                                'header_row': {'description': '右表表头行（Excel '
                                                                                              '1-based）；右表表头不在第 '
                                                                                              '1 行时传',
                                                                               'type': 'integer'},
                                                                'how': {'description': '连接类型；left '
                                                                                       '以左表为基准，anti 只返回未匹配行',
                                                                        'enum': ['left',
                                                                                 'inner',
                                                                                 'right',
                                                                                 'outer',
                                                                                 'left_anti',
                                                                                 'right_anti'],
                                                                        'type': 'string'},
                                                                'leftOn': {'description': 'left_on 的别名',
                                                                           'type': 'string'},
                                                                'left_on': {'description': '左表键：列名或列名数组',
                                                                            'items': {'type': 'string'},
                                                                            'type': ['string', 'array']},
                                                                'on': {'description': '同名连接键：列名或列名数组',
                                                                       'items': {'type': 'string'},
                                                                       'type': ['string', 'array']},
                                                                'path': {'description': 'file_path 的别名',
                                                                         'type': 'string'},
                                                                'rightOn': {'description': 'right_on 的别名',
                                                                            'type': 'string'},
                                                                'right_on': {'description': '右表键：列名或列名数组',
                                                                             'items': {'type': 'string'},
                                                                             'type': ['string', 'array']},
                                                                'sheet': {'description': '同簿右表名',
                                                                          'type': 'string'},
                                                                'sheet_name': {'description': 'sheet 的别名',
                                                                               'type': 'string'}},
                                                 'type': ['object', 'string']},
                                        'logic': {'description': '条件组合：and（默认）/ or / '
                                                                 'not（对单个条件整体取反，仅接受恰好一个条件）',
                                                  'enum': ['and', 'or', 'not'],
                                                  'type': 'string'},
                                        'margins': {'description': 'pivot 追加合计行与合计列（Excel 总计）；合计标签用 '
                                                                   'margins_name，默认「合计」。',
                                                    'type': 'boolean'},
                                        'margins_name': {'description': 'pivot 合计行/列标签，默认「合计」',
                                                         'type': 'string'},
                                        'max_files': {'description': 'files/relationships 的文件数上限',
                                                      'type': 'integer'},
                                        'max_rows': {'description': '行数上限（规范名）：profile/quality '
                                                                    '为采样行数上限，filter/aggregate/distinct/pivot '
                                                                    '为结果行数/组数/条目数上限。',
                                                     'type': 'integer'},
                                        'mode': {'description': 'profile/quality 数据框全貌；filter 筛行；aggregate '
                                                                '分组汇总；pivot '
                                                                '二维透视(index×columns×values)；distinct '
                                                                '单列取值分布；relationships 跨文件列关联；files 扫目录',
                                                 'enum': ['profile',
                                                          'quality',
                                                          'filter',
                                                          'aggregate',
                                                          'distinct',
                                                          'pivot',
                                                          'relationships',
                                                          'files'],
                                                 'type': 'string'},
                                        'operator': {'description': 'eq/ne/gt/ge/lt/le/contains/not_contains/regex/not_regex/in/not_in/between/isnull/notnull/startswith/endswith；也接受 '
                                                                    '=、==、!=、not（→ne）',
                                                     'type': 'string'},
                                        'query': {'description': 'files 模式按文件名或路径搜索', 'type': 'string'},
                                        'sample_rows': {'description': 'relationships '
                                                                       '的每文件采样行数；profile/quality 下作 max_rows '
                                                                       '别名（采样行数上限）',
                                                        'type': 'integer'},
                                        'sheet': {'description': '工作表名，可用 sheet '
                                                                 '别名；多表时原则必填，省略时仅当只有一张可见表或列证据唯一命中可见数据表才自动绑定（见 '
                                                                 'warnings/resolved_sheet）；隐藏表列碰撞仍 '
                                                                 'SHEET_REQUIRED',
                                                  'type': 'string'},
                                        'sort_by': {'description': 'filter/aggregate '
                                                                   '的结果排序列：数据列名、分组键或聚合输出列名（如 '
                                                                   '金额_sum）；传源列名会自动映射到其唯一聚合输出列',
                                                    'type': 'string'},
                                        'value': {'description': 'filter/aggregate/pivot 单条件的比较值（配合 '
                                                                 'column+operator）'},
                                        'values': {'description': 'pivot 值列：列名或列名数组，或等价 JSON 字符串（自动解析）。'
                                                                  '示例：["销售额(万元)"]',
                                                   'type': ['string', 'array']}},
                         'type': 'object'},
 'compare_spreadsheets': {'additionalProperties': False,
                          'properties': {'alignment': {'description': 'position 按单元格行列坐标；key 需 '
                                                                      'key_columns。二者不能同时用。有 SKU/编号等业务主键时用 '
                                                                      'key，增删行不要用 position。',
                                                       'enum': ['position', 'key'],
                                                       'type': 'string'},
                                         'file_a': {'type': 'string'},
                                         'file_b': {'description': '另一个文件；同一文件比两张表时可省略并提供不同 sheet_a/sheet_b',
                                                    'type': 'string'},
                                         'ignore_style': {'default': True,
                                                          'description': 'false 时额外按坐标比较样式、布局、规则和对象。',
                                                          'type': 'boolean'},
                                         'key_columns': {'description': 'alignment=key 时的业务主键，例如 ["SKU"]。',
                                                         'items': {'type': 'string'},
                                                         'type': 'array'},
                                         'max_diffs': {'default': 500, 'type': 'integer'},
                                         'sheet_a': {'type': 'string'},
                                         'sheet_b': {'type': 'string'}},
                          'type': 'object'},
 'manage_spreadsheet_versions': {'additionalProperties': False,
                                 'properties': {'action': {'description': 'list 只读，read/plan '
                                                                          '目录可见；checkpoint/restore/delete '
                                                                          '写入工作区，执行层按 action 拦截。',
                                                           'enum': ['list',
                                                                    'checkpoint',
                                                                    'restore',
                                                                    'delete'],
                                                           'type': 'string'},
                                                'expected_version': {'description': '建议传最近一次成功读/写返回的 '
                                                                                    'content_version，对应本次计算所依据的数据。未传时提交层只会尝试使用本轮已观察版本；没有观察版本就拒绝。selection '
                                                                                    '写回使用 '
                                                                                    'selection.content_version；restore '
                                                                                    '须显式 expected_version。',
                                                                     'type': 'string'},
                                                'file_path': {'type': 'string'},
                                                'label': {'type': 'string'},
                                                'revision_id': {'type': 'string'}},
                                 'required': ['file_path', 'action'],
                                 'type': 'object'},
 'split_spreadsheet': {'additionalProperties': False,
                       'properties': {'expected_version': {'description': '可选；要求拆分基于这次读取到的源文件版本。',
                                                           'type': 'string'},
                                      'filename_template': {'default': '{key}',
                                                            'description': '文件名模板：{key}=分组键、{stem}=源文件名去扩展名。自动补 '
                                                                           '.xlsx。',
                                                            'type': 'string'},
                                      'header_row': {'description': '列头所在行号（Excel 行号，1-based），默认自动检测',
                                                     'type': 'integer'},
                                      'max_files': {'description': '拆分文件数上限（默认 50）。超过即拒绝，不写任何文件。',
                                                    'type': 'integer'},
                                      'output_dir': {'default': 'outputs',
                                                     'description': '输出目录（工作区相对路径）。不能是 uploads/。',
                                                     'type': 'string'}},
                       'required': ['file_path', 'by_column'],
                       'type': 'object'},
 'trace_spreadsheet_formulas': {'additionalProperties': False,
                                'properties': {'depth': {'default': 2,
                                                         'description': '仅 mode=trace：追踪深度 1-5',
                                                         'maximum': 5,
                                                         'minimum': 1,
                                                         'type': 'integer'},
                                               'detail': {'description': '仅 mode=map：输出详略',
                                                          'enum': ['summary', 'full'],
                                                          'type': 'string'},
                                               'direction': {'description': '仅 mode=trace：追踪方向',
                                                             'enum': ['precedents', 'dependents', 'both'],
                                                             'type': 'string'},
                                               'file_path': {'type': 'string'},
                                               'mode': {'enum': ['map', 'trace', 'impact'], 'type': 'string'},
                                               'scope': {'description': 'impact 范围：all=所有工作表，sheet=目标工作表',
                                                         'enum': ['all', 'sheet'],
                                                         'type': 'string'},
                                               'target': {'description': 'Excel A1（1-based 闭区间）。 整列请写 A:A '
                                                                         '而不是 A；整行请写 1:1 而不是 '
                                                                         "1；含空格的表名必须用单引号：'My "
                                                                         "Sheet'!A1；表名中的单引号写成两个：'O''Brien'!A1；并集用英文逗号，不要用分号、中文逗号或空格；parse_ref "
                                                                         '使用 A1；需要相对地址时用 parse_r1c1(text, '
                                                                         'base_row, base_col)；不要前置等号。 规范名是 '
                                                                         'target，如 产品表!B2；trace/impact '
                                                                         '必填，map 不接受。',
                                                          'type': 'string'}},
                                'type': 'object'}}

# Canonical required fields must also be declared under additionalProperties=false.
QUERY_SCHEMAS["split_spreadsheet"]["properties"].update({
    "file_path": {"type": "string"}, "by_column": {"type": "string"}, "sheet": {"type": "string"}})
QUERY_SCHEMAS["manage_spreadsheet_versions"]["properties"]["limit"] = {"type":"integer","minimum":1,"maximum":500}

# Runtime domain functions have accepted these aliases for older SDK/native
# callers.  Declare them on the V2 wire schemas as well, then fold them at the
# WorkbookService boundary.  Canonical names remain first in every schema and
# conflicting canonical/alias values are rejected during folding.
_ANALYZE_ALIASES = {
    "path": "file_path", "sheet_name": "sheet", "content_version": "expected_version",
    "groupBy": "group_by", "aggs": "aggregations", "maxRows": "max_rows",
    "sampleRows": "sample_rows", "sortBy": "sort_by", "headerRow": "header_row",
    "filePaths": "file_paths", "dupOnly": "dup_only", "lookup": "join",
}
for _alias, _canonical in _ANALYZE_ALIASES.items():
    _node = QUERY_SCHEMAS["analyze_spreadsheet"]["properties"].get(_canonical)
    if isinstance(_node, dict):
        QUERY_SCHEMAS["analyze_spreadsheet"]["properties"].setdefault(_alias, deepcopy(_node))
_join_props = QUERY_SCHEMAS["analyze_spreadsheet"]["properties"]["join"].get("properties", {})
for _alias, _canonical in {"path": "file_path", "sheet_name": "sheet", "leftOn": "left_on", "rightOn": "right_on"}.items():
    _node = _join_props.get(_canonical)
    if isinstance(_node, dict):
        _join_props.setdefault(_alias, deepcopy(_node))

_QUERY_ALIASES = {
    "compare_spreadsheets": {
        "path": "file_a", "other_path": "file_b", "sheet": "sheet_a", "other_sheet": "sheet_b",
        "keyColumns": "key_columns", "maxDifferences": "max_diffs",
    },
    "manage_spreadsheet_versions": {"path": "file_path", "content_version": "expected_version"},
    "split_spreadsheet": {
        "path": "file_path", "column": "by_column", "sheet_name": "sheet", "content_version": "expected_version",
        "maxFiles": "max_files", "outputDir": "output_dir", "filenameTemplate": "filename_template",
        "headerRow": "header_row",
    },
    "trace_spreadsheet_formulas": {"path": "file_path", "range": "target"},
}
for _query_name, _aliases in _QUERY_ALIASES.items():
    _props = QUERY_SCHEMAS[_query_name].setdefault("properties", {})
    for _alias, _canonical in _aliases.items():
        _node = _props.get(_canonical)
        if isinstance(_node, dict):
            _props.setdefault(_alias, deepcopy(_node))
# Alias spellings can satisfy the same path/key requirements as their
# canonical counterparts.  Keep the mode/action requirements unchanged.
QUERY_SCHEMAS["manage_spreadsheet_versions"]["required"] = ["action"]
QUERY_SCHEMAS["manage_spreadsheet_versions"]["anyOf"] = [
    {"required": ["file_path"]}, {"required": ["path"]}
]
QUERY_SCHEMAS["split_spreadsheet"]["required"] = []
QUERY_SCHEMAS["split_spreadsheet"]["anyOf"] = [
    {"required": ["file_path", "by_column"]},
    {"required": ["file_path", "column"]},
    {"required": ["path", "by_column"]},
    {"required": ["path", "column"]},
]

# ── 统一参数合同（与运行时 _maybe_json 归一保持一致）────────────────────────
# analyze_spreadsheet 的结构化参数一律接受两等价形式：结构化对象/数组，或
# JSON 字符串（运行时自动解析）。schema 的 type 与 description 必须一致：
# 描述说接受 JSON 字符串的字段，type 必须包含 "string"，反之亦然。
_QUERY_JSON_STRING_FIELDS = ("aggregations", "group_by", "index", "columns",
                             "values", "conditions", "join", "file_paths", "include")
for _field in _QUERY_JSON_STRING_FIELDS:
    _node = QUERY_SCHEMAS["analyze_spreadsheet"]["properties"][_field]
    assert "string" in (["string"] if _node.get("type") == "string" else list(_node.get("type") or [])), _field

# 结构化参数的期望形状与正确示例。schema 校验失败与运行时解析失败统一用它
# 给出一句中文可执行错误，而不是整段倾倒 jsonschema 原文。
QUERY_ARG_SHAPES: dict[str, tuple[str, str]] = {
    "aggregations": (
        "{列名: 函数或函数数组} 对象、[{column, func}] 数组或等价 JSON 字符串（自动解析）",
        '{"销售额(万元)": ["sum", "mean", "max", "min"]}',
    ),
    "group_by": (
        "列名、列名数组、{column, transform} 派生键对象或等价 JSON 字符串（自动解析）",
        '["区域", "年份"]',
    ),
    "index": (
        "列名、列名数组、{column, transform} 派生键对象或等价 JSON 字符串（自动解析）",
        '["区域"]',
    ),
    "columns": (
        "列名、列名数组、{column, transform} 派生键对象或等价 JSON 字符串（自动解析）",
        '["年份"]',
    ),
    "values": (
        "列名或列名数组，或等价 JSON 字符串（自动解析）",
        '["销售额(万元)"]',
    ),
    "conditions": (
        "[{column, operator, value}] 数组、单条件对象或等价 JSON 字符串（自动解析）",
        '[{"column": "年份", "operator": "eq", "value": 2023}]',
    ),
    "join": (
        "{sheet|file_path, on|left_on+right_on, columns?} 对象或等价 JSON 字符串（自动解析）",
        '{"sheet": "产品", "on": "产品ID", "columns": ["名称"]}',
    ),
    "file_paths": (
        "文件路径字符串数组或等价 JSON 字符串（自动解析）",
        '["a.xlsx", "b.xlsx"]',
    ),
    "include": (
        "字符串数组或等价 JSON 字符串（自动解析）",
        '["columns"]',
    ),
}


def query_arg_shape_error(field: str) -> str | None:
    """字段期望形状 + 正确示例的一句中文说明；未知字段返回 None。"""
    shape = QUERY_ARG_SHAPES.get(field)
    if shape is None:
        return None
    return f"{field} 需要 {shape[0]}。示例：{shape[1]}"


def query_schema_error_message(exc: Exception) -> str:
    """jsonschema.ValidationError → 简洁中文（期望形状 + 正确示例）。

    禁止整段倾倒 jsonschema 原文；未知字段退回一句通用中文指引。
    """
    path = [item for item in (getattr(exc, "absolute_path", None) or [])]
    field = next((str(item) for item in reversed(path) if isinstance(item, str)), "")
    where = ".".join(str(item) for item in path) if path else "参数"
    hint = query_arg_shape_error(field)
    if hint is not None:
        return hint
    validator = str(getattr(exc, "validator", "") or "")
    if validator == "required":
        missing = ", ".join(str(item) for item in (getattr(exc, "validator_value", None) or []))
        return f"{where} 缺少必填字段：{missing}；请补全后重试"
    if validator == "additionalProperties":
        from difflib import get_close_matches

        instance = getattr(exc, "instance", None)
        declared = getattr(exc, "schema", None) or {}
        declared_names = set(declared.get("properties") or {}) if isinstance(declared, dict) else set()
        extra = sorted(str(key) for key in (instance or {}) if str(key) not in declared_names) if isinstance(instance, dict) else []
        names = f"{'、'.join(extra)}" if extra else "未声明的字段"
        hints = []
        for key in extra:
            matches = get_close_matches(key, sorted(declared_names), n=1, cutoff=0.45)
            if matches:
                hints.append(f"{key}→{matches[0]}")
        suffix = f"；可能是别名：{'、'.join(hints)}" if hints else ""
        return f"不支持的字段：{names}；请只传工具 schema 声明的字段{suffix}"
    if validator == "enum":
        allowed = ", ".join(str(item) for item in (getattr(exc, "validator_value", None) or []))
        return f"{where} 取值不在允许范围内（{allowed}）；请改用允许的取值"
    if validator == "type":
        expected = getattr(exc, "validator_value", None)
        expected_types = [expected] if isinstance(expected, str) else list(expected or [])
        if {"object", "array"} & set(expected_types):
            return f"{where} 类型不对，期望 {'/'.join(expected_types)}；请传结构化对象/数组，或等价 JSON 字符串（自动解析）"
        return f"{where} 类型不对，期望 {'/'.join(expected_types) or repr(expected)}；请改传正确类型"
    return f"{where} 不符合工具参数 schema 要求；请对照工具 schema 修正后重试"
