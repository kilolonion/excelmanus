"""Canonical V2 query schemas; field names are shared with workbook observation."""
QUERY_SCHEMAS = {'analyze_spreadsheet': {'additionalProperties': False,
                         'properties': {'aggfunc': {'description': 'pivot 聚合函数，默认 sum', 'type': 'string'},
                                        'aggregations': {'description': 'aggregate 的聚合规格：{列名: 函数或函数数组} 或 '
                                                                        '[{column, func}] 数组，也接受 JSON 字符串；函数 '
                                                                        'sum/count/mean/min/max/median/std/nunique/first/last；"*" '
                                                                        '表示行计数；TopN 效果用 sort_by+limit 不是函数',
                                                         'type': 'object'},
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
                                                                   '下为选中列名数组；传列名、数组或对象，不要传 JSON 字符串'},
                                        'conditions': {'description': '[{column,operator,value}]；isnull/notnull '
                                                                      '不需要 value；between 用 [下界,上界]，in/not_in '
                                                                      '用数组；conditions=[] 表示不过滤。',
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
                                                       'type': 'array'},
                                        'directory': {'description': 'files/relationships 的扫描目录，默认工作区根',
                                                      'type': 'string'},
                                        'dup_only': {'description': 'distinct 只看重复取值（count>1）并附行号',
                                                     'type': 'boolean'},
                                        'expected_version': {'description': '可选；要求分析基于这次读取到的 '
                                                                            'content_version。',
                                                             'type': 'string'},
                                        'file_path': {'type': 'string'},
                                        'file_paths': {'description': 'relationships 的目标文件列表；单文件直接用 '
                                                                      'file_path',
                                                       'items': {'type': 'string'},
                                                       'type': 'array'},
                                        'group_by': {'description': 'aggregate 的分组列名或列名数组（pivot 中作 index '
                                                                    '别名）；也支持日期派生键对象 {column, '
                                                                    'transform}，transform=year|quarter|month|year_month|date|week|hour（按月份聚合传 '
                                                                    '{"column":"日期","transform":"year_month"}）；不传则整体汇总；传列名或对象，不要传 '
                                                                    'JSON 字符串'},
                                        'header_row': {'description': '列头所在行号（Excel 行号，1-based，第 1 行 = '
                                                                      '1），默认自动检测；表单类文档传 -1',
                                                       'type': 'integer'},
                                        'include': {'description': 'files 模式附加维度',
                                                    'items': {'type': 'string'},
                                                    'type': 'array'},
                                        'index': {'description': 'pivot 行维度：列名或列名数组，也支持日期派生键对象 {column, '
                                                                 'transform}（同 group_by）；传列名、数组或对象，不要传 JSON '
                                                                 '字符串'},
                                        'join': {'additionalProperties': False,
                                                 'description': 'aggregate/pivot '
                                                                '跨表连接；支持单列或等长度多列键、left/inner/right/outer/left_anti/right_anti。left '
                                                                '连接保持 VLOOKUP 首匹配语义，其余连接保留键重复。连接列可参与 '
                                                                'group_by/aggregations/conditions。也接受 JSON '
                                                                '字符串。',
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
                                                 'type': 'object'},
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
                                        'values': {'description': 'pivot 值列：列名或列名数组'}},
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
for _alias in ("path", "leftOn", "rightOn", "sheet_name"):
    QUERY_SCHEMAS["analyze_spreadsheet"]["properties"]["join"]["properties"].pop(_alias, None)

QUERY_SCHEMAS["analyze_spreadsheet"]["properties"]["aggregations"]["type"] = ["object", "array"]
for _field in ("aggregations", "join"):
    _node = QUERY_SCHEMAS["analyze_spreadsheet"]["properties"][_field]
    _node["description"] = _node.get("description", "").replace("也接受 JSON 字符串", "只接受结构化对象/数组")
