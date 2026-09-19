# Bench

评测只放这里。套件 JSON、夹具生成器、跑手都在本目录；跑次产物在 `outputs/`，不要另开 `evals/` 之类平行树。

```
bench/
  README.md
  run.ps1                      # 把 test.env 凭据导入隔离主库，生成夹具，再跑 bench
  cases/                       # 全部 suite JSON；--all 只跑 include_in_all 未关闭的
    suite_smoke.json           # 短冒烟，有断言
    suite_write_approval.json  # 审批链路，有断言
    suite_experiential.json    # 体验向长套件，无标准答案，不进 --all
    suite_realistic.json       # 真实办公任务套件，有结果断言，不进 --all
  fixtures/
    build_experiential.py      # 生成脏办公文件
    build_realistic.py         # 生成真实办公夹具（多表/CSV/docx/大表）+ answers.json
    jev_calibration/           # 片 D 离线合成夹具（固定 JSON，不打网；未签字）
    experiential/  realistic/  # 生成物，不入库
  reports/                     # 框架审计、runbook、各轮分析
  analyze_run.py               # run_*.json 展开成复盘 Markdown（含 system prompt/token）
  summarize_runs.py            # 一批 run_*.json 的效率指标汇总表
```

## 两类套件

| 套件 | 目的 | 判断方式 | `--all` |
| --- | --- | --- | --- |
| `suite_smoke` / `suite_write_approval` | 链路是否通 | `assertions` | 是 |
| `suite_experiential` | 真实/含糊/怪异请求下的体验 | 事后读对话，不设标准答案 | 否（`include_in_all: false`） |
| `suite_realistic` | 真实办公任务的交付正确性 + 效率 | `output_checks` 结果断言 + review_focus 人工复盘 | 否 |

`realistic` 套件 32 条分五波（wave-r1…r4 只读/清洗/格式/多轮边界，wave-r5 为补洞扩展：
条件格式、多条件过滤、省份口径、代码模式对照、长程撤销、双条件均薪）：只读问答、公式/汇总/透视、脏数据清洗、
格式化、跨文件对比、大表、缺文件/隐私边界、多轮指代、docx 交付。断言值用
`@answers:key` 引用 `fixtures/realistic/answers.json`；`auto_approve=accept`
模拟网页默认逐次审批。效率预算（max_*）为 warn-only 告警，不判 fail，详见
`cases/README.md`。nightly 可用 `-Seed <int>` 生成同构异值夹具防过拟合
（换 seed 后必须用新 `answers.json` 评分）。

体验套件分四轮，共 24 条，覆盖探查、脏数据、多轮指代、Word/Excel/CSV/图、只读/计划/代码模式、对比、撤销、缺文件、隐私、越权拒绝、自相矛盾指令。`expected.review_focus` 只是阅读提纲，不会触发 golden 比对。

夹具构造意图写在 `fixtures/build_experiential.py`：金额、日期、区域、公式可以互相打架。

## 字段

见 `cases/README.md`。体验套件额外约定：

- `include_in_all: false`：`--all` 跳过
- `scoring: none`：不写断言、不写 `golden_file`
- `expected.lens` / `expected.review_focus`：事后分析用
- `tags` 含 `wave-1` … `wave-4`

## 运行

```powershell
# 短套件
uv run python -m excelmanus.bench --all
uv run python -m excelmanus.bench --suite bench/cases/suite_smoke.json

# 体验向：先生成夹具；凭据来自仓库根 test.env（导入隔离主库，不是产品 .env）
.\bench\run.ps1 -CaseId E15              # 单条（推荐：跑完读对话再下一条）
.\bench\run.ps1 -Wave 1                  # 整波

# 真实办公套件（模型运行需先批准，流程见 reports/02-realistic-runbook.md）
.\bench\run.ps1 -Suite bench/cases/suite_realistic.json -CaseId R01
.\bench\run.ps1 -Suite bench/cases/suite_realistic.json -Wave r1
.\bench\run.ps1 -Suite bench/cases/suite_realistic.json -BigRows 20000   # 调试缩小大表
.\bench\run.ps1 -TurnTimeout 600         # 覆盖单轮硬超时（套件默认 900s）
.\bench\run.ps1 -Suite bench/cases/suite_realistic.json -Strict   # 严格模式：效率超限也判 fail
```

## 运行分级

| 层级 | 命令 | 用途 | 口径 |
|---|---|---|---|
| L1 CI 门禁 | `--all`（3 条冒烟） | 链路是否通，每次提交 | error 判 fail |
| L2 nightly 回归 | `--suite suite_realistic.json` | 32 条交付正确性 | 默认 warn-only；收紧用 `-Strict` |
| L3/L4 波次 | `-Wave r1…r5` | 定点复盘某一类任务 | 同 L2 |
| L5 开放体验 | `--suite suite_experiential.json` | 含糊/对抗/安全，只人工读 digest | 不评分 |

`--all` 故意只含冒烟：长套件费模型与时间，必须显式指定。

`run.ps1` 把仓库根 `test.env` 里的 `url`/`key`/`model` 当作**评测凭据清单**：规范化网关 URL 后写入本次运行的隔离主库（`outputs/.../runtime-home`）。未写 `model` 时默认 `mimo-v2.5-pro`。夹具生成器按套件名自动选（realistic/experiential，可用 `-Fixtures` 覆盖）。未显式给 `-OutputDir` 时按套件名落到 `outputs/<suite>/`，再按用例 `wave-*` 标签落到子目录。

产物（每个用例）：

- `conversations/{case_id}.json`：伪造前端 transcript + 会话库 + 引擎记忆
- `conversations/{case_id}.digest.md`：**分析摘要**——一轮一节，回复全文、工具调用
  （参数/返回截断）、LLM 调用指标（耗时/token/上下文规模）、思考摘要、交互事件、
  系统提示注入概况。事后评审先读这份，要细节再钻下面两份全量。
- `run_*.json`：全量执行记录（llm_calls 带每次请求 messages 与 usage）
- `workfiles/{suite}/{case_id}/`：用例独立工作区（每次跑前清空）

事后阅读优先看 digest 与伪造前端 `transcript`，对照该 case 的 `review_focus`。不要用单元格是否等于标准答案当通过条件。

注意 `run.ps1` 含中文注释，必须保持 UTF-8 with BOM 保存，否则 Windows PowerShell 5.1
按 ANSI 解析直接报语法错误（pwsh 7 不受影响）。
