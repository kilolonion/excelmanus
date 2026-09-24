# 模型评测（Bench）

Bench 用于通过实际对话、工具调用和产出文件评估 ExcelManus。适用版本：1.8.1 源码；更新日期：2026-09-21。

[文档导航](../docs/README.md) · [套件字段](cases/README.md) · [夹具生成](fixtures/README.md)

这里的对话评测会调用所配置的模型服务，可能产生费用。它与无需真实模型的单元测试、Jev 离线合成检查是不同的验证方式。

## 套件选择

| 套件 | 当前用例数 | 目的与判断方式 | 包含在 `--all` |
| --- | --- | --- | --- |
| `suite_smoke.json` | 2 | 基础链路与声明式断言 | 是 |
| `suite_write_approval.json` | 1 | 审批交互 | 是 |
| `suite_experiential.json` | 24 | 含糊、异常、多轮和跨文件任务；人工阅读对话，不设标准答案 | 否 |
| `suite_realistic.json` | 32 | 办公任务；结果断言与人工复盘 | 否 |
| `suite_prompt_contract.json` | 5 | 提示词与工具契约场景；运行时计算检查项 | 否 |

`--all` 仅运行 `include_in_all` 未关闭的套件，目前共 3 个用例。增加套件后，范围以 JSON 文件为准。长套件需要显式选择，不应把短套件通过视为全面功能验收。

## 准备隔离配置

Bench 从当前 data home 的主数据库读取激活模型。`--import-env` 是显式的一次性凭据导入，执行后退出；它不会把 `.env` 变回产品配置源，也不会自行保证与日常数据隔离。

建议使用独立目录。以下命令在仓库根目录运行，`test.env` 是本机凭据清单，不应提交到仓库：

```bash
export EXCELMANUS_HOME="$PWD/outputs/bench-local/runtime-home"
uv run python -m excelmanus.bench --import-env test.env --model YOUR_MODEL_ID
uv run python -m excelmanus.bench --all --output-dir outputs/bench-local
```

清单可使用 `url`、`key`、`model`，或对应的 `EXCELMANUS_BASE_URL`、`EXCELMANUS_API_KEY`、`EXCELMANUS_MODEL`。如果清单已包含模型，导入时可省略 `--model`。隔离目录应同时用于导入和后续运行；更换终端时重新设置。完成评测后，在该终端执行 `unset EXCELMANUS_HOME` 可恢复默认目录选择。

## 生成夹具并运行

体验和办公套件使用本地生成的输入文件，先按 [夹具说明](fixtures/README.md) 准备：

```bash
uv run python bench/fixtures/build_experiential.py
uv run python bench/fixtures/build_realistic.py
```

在已配置隔离 data home 的终端运行：

```bash
uv run python -m excelmanus.bench --suite bench/cases/suite_experiential.json --case E15
uv run python -m excelmanus.bench --suite bench/cases/suite_realistic.json --case R01
uv run python -m excelmanus.bench --suite bench/cases/suite_realistic.json --wave r1
uv run python -m excelmanus.bench --suite bench/cases/suite_prompt_contract.json
uv run python -m excelmanus.bench --help
```

可重复传入 `--case`，也可通过 `--concurrency`、`--suite-concurrency` 控制并发。默认均为 1；提高并发会增加模型请求和资源占用。

单轮超时优先级为 case → suite → `--turn-timeout`，`0` 表示不限制。用例工作目录在每次运行前重新准备，不能用它保存独立的用户文件。

### Windows PowerShell 入口

`bench/run.ps1` 会读取凭据、创建隔离主库、选择夹具生成器并调用同一个 Bench 运行器：

```powershell
.\bench\run.ps1 -CaseId E15 -Model YOUR_MODEL_ID
.\bench\run.ps1 -Wave 1 -Model YOUR_MODEL_ID
.\bench\run.ps1 -Suite bench/cases/suite_realistic.json -CaseId R01 -Model YOUR_MODEL_ID
.\bench\run.ps1 -Suite bench/cases/suite_realistic.json -Wave r1 -Strict -Model YOUR_MODEL_ID
```

脚本读取仓库根目录的 `test.env`，可用 `-TestEnv` 替换。显式传入 `-Model` 或在清单中填写模型，避免依赖脚本内的回退模型。`-BigRows` 可缩小办公套件的大表，`-Seed` 可生成不同数据；更换种子后，必须使用同一次生成的 `answers.json`。

修改该脚本时保留 UTF-8 BOM，确保 Windows PowerShell 5.1 正确读取中文。

## 如何解释结果

- **正确性**：由 error 级过程断言和 `output_checks` 判定。断言范围外的文件保真、界面体验等仍需单独检查。
- **效率**：`max_*` 预算默认产生 warning，不改变正确性结论；`--strict-efficiency` 或 PowerShell `-Strict` 会将超限纳入失败。
- **体验套件**：`scoring: none`，按 `review_focus` 人工阅读回复、操作和交付内容；不能将运行结束等同于业务通过。
- **结果解释**：单次运行结果只适用于所记录的代码、模型、配置和夹具，不能直接用作新版本通过证明。

办公套件通过 `@answers:key` 引用 `fixtures/realistic/answers.json`。标准答案由评测器读取；Agent 不应读取答案或套件定义来完成任务。完整字段与反绕过规则见 [套件文档](cases/README.md)。

## 产物与目录

| 位置 | 内容 |
| --- | --- |
| `conversations/{case_id}.digest.md` | 每轮回复、工具摘要、交互事件和请求指标，适合先阅读 |
| `conversations/{case_id}.json` | 模拟前端 transcript、会话记录和引擎记忆 |
| `run_*.json` | 完整执行记录，包括模型请求与 usage |
| `workfiles/{suite}/{case_id}/` | 用例独立工作区 |

默认 CLI 产物目录为 `outputs/bench`。PowerShell 入口默认按套件和 wave 组织到 `outputs/`。`analyze_run.py` 与 `summarize_runs.py` 用于复盘和指标汇总。

执行记录可能包含完整对话、文件内容、请求参数及模型输出。分享前检查并脱敏，不要将含真实业务数据或凭证的运行产物提交到仓库。
