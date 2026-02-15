# ExcelManus Bench 测试指导

## 概述

`bench/` 是 ExcelManus 的端到端基准测试系统，通过真实 LLM 调用验证 Agent 的路由、工具选择、执行效率和输出质量。

核心流程：**加载用例 JSON → 创建独立 Engine → 调用 `engine.chat()` → 收集事件轨迹 → 输出结构化 JSON 日志**。

---

## 目录结构

```
bench/
├── README.md                  # 本文档
├── cases/                     # 测试套件（JSON）
│   ├── suite_01_基础读取类.json
│   ├── suite_02_数据分析类.json
│   ├── ...
│   └── suite_basic.json
├── external/                  # 外部 Benchmark 数据集（gitignore）
│   └── spreadsheetbench/      # SpreadsheetBench (NeurIPS 2024)
├── run_3way_ab.sh             # 三模式 AB 对比脚本
└── analyze_3way.py            # AB 对比分析脚本
```

---

## 快速开始

### 1. 运行单个套件

```bash
# 基础读取类（3 用例，约 20s）
.venv/bin/python -m excelmanus.bench \
    --suite bench/cases/suite_01_基础读取类.json \
    --output-dir outputs/bench_test

# 基础读写套件（8 用例，含问候场景，约 70s）
.venv/bin/python -m excelmanus.bench \
    --suite bench/cases/suite_basic.json \
    --output-dir outputs/bench_test
```

### 2. 运行全部套件

```bash
.venv/bin/python -m excelmanus.bench --all --output-dir outputs/bench_full
```

### 3. 单条消息快速测试

```bash
.venv/bin/python -m excelmanus.bench \
    --message "读取 examples/bench/stress_test_comprehensive.xlsx 中销售明细的前10行"
```

### 4. 三模式 AB 对比（窗口感知评估）

```bash
# 运行 OFF / ENRICHED / ANCHORED 三种模式
bash bench/run_3way_ab.sh

# 指定套件
bash bench/run_3way_ab.sh --suites bench/cases/suite_01_基础读取类.json,bench/cases/suite_basic.json

# 分析结果
.venv/bin/python bench/analyze_3way.py outputs/bench_3way_XXXXXXXX
```

---

## 测试套件总览

### 功能验证类（suite_01 ~ suite_07）

| 套件 | 用例数 | 测试目标 |
|------|--------|---------|
| `suite_01_基础读取类` | 3 | 读取前N行、统计 sheet 信息、筛选数据 |
| `suite_02_数据分析类` | 4 | 分组统计、排序、占比计算、描述统计 |
| `suite_03_格式化与样式类` | 3 | 标题加粗、交替行底色、列宽调整 |
| `suite_04_图表生成类` | 3 | 柱状图、折线图、饼图生成 |
| `suite_05_公式与计算类` | 3 | 写入公式、衍生列、条件计算 |
| `suite_06_跨表操作类` | 3 | 跨 sheet 汇总、多表合并、引用对比 |
| `suite_07_高难度综合类` | 3 | 多步骤复合任务、大文件处理 |

### 样式专项类（suite_08 ~ suite_14）

| 套件 | 用例数 | 测试目标 |
|------|--------|---------|
| `suite_08_边框与线条` | 3 | 边框样式、线条粗细 |
| `suite_09_条件格式` | 4 | 条件着色、阈值标记 |
| `suite_10_合并单元格与布局` | 3 | 合并区域、居中、布局 |
| `suite_11_字体与填充` | 3 | 字体大小/颜色、背景填充 |
| `suite_12_数字格式` | 3 | 千分位、百分比、日期格式 |
| `suite_13_打印与页面布局` | 2 | 页边距、打印区域 |
| `suite_14_综合样式挑战` | 2 | 多种样式组合应用 |

### 场景专项类

| 套件 | 用例数 | 测试目标 |
|------|--------|---------|
| `suite_15_多轮对话` | 4 | 多轮上下文保持、追问和修正 |
| `suite_basic` | 8 | **冒烟测试**：覆盖扫描/读取/筛选/统计/聚合/问候 |
| `suite_window_perception_ab` | 3 | 窗口感知启用/禁用对比 |
| `suite_window_perception_complex` | 3 | 多轮窗口感知记忆验证 |
| `suite_window_perception_ultra` | 4 | 6+ 轮极端多窗口场景 |
| `suite_ab_include_compare` | 2 | `read_excel` include 参数效率对比 |

---

## 推荐测试流程

### 日常开发验证

修改代码后运行最小集确认无回归：

```bash
# 冒烟测试（8 用例，约 70s，覆盖核心路径 + 问候场景）
.venv/bin/python -m excelmanus.bench \
    --suite bench/cases/suite_basic.json \
    --output-dir outputs/bench_smoke
```

### 提示词/路由改动验证

提示词或路由逻辑变更后，重点关注：

```bash
# 基础读取 + 基础读写（11 用例）
.venv/bin/python -m excelmanus.bench \
    --suite bench/cases/suite_01_基础读取类.json \
    --suite bench/cases/suite_basic.json \
    --output-dir outputs/bench_prompt_check
```

**关键检查项：**
- `case_simple_greeting`：应 0 工具调用、1 迭代、纯文本回复
- 所有读取类用例：首轮 LLM 应直接发出 `tool_calls`，无空承诺文字
- `case_multi_sheet_compare`：应直接从路由预览回答，0 工具调用

### 窗口感知改动验证

```bash
bash bench/run_3way_ab.sh
.venv/bin/python bench/analyze_3way.py outputs/bench_3way_XXXXXXXX
```

### 全量回归

发版前运行全量 20 个套件：

```bash
.venv/bin/python -m excelmanus.bench --all --output-dir outputs/bench_release_vX
```

---

## 用例格式

### 单轮用例

```json
{
  "suite_name": "套件名称",
  "description": "套件描述",
  "cases": [
    {
      "id": "case_read_top10",
      "name": "读取前10行",
      "message": "读取 examples/bench/stress_test_comprehensive.xlsx 中销售明细的前10行",
      "tags": ["data_read", "header_detection"]
    }
  ]
}
```

### 多轮用例

```json
{
  "id": "multi_turn_01",
  "name": "多轮追问",
  "messages": [
    "读取销售明细的前10行",
    "按城市分组统计总金额",
    "把结果画成柱状图"
  ],
  "tags": ["multi_turn", "chart"]
}
```

### 字段说明

| 字段 | 必需 | 说明 |
|------|------|------|
| `id` | ✅ | 用例唯一标识，用于跨模式对齐 |
| `name` | ✅ | 用例中文名称 |
| `message` | 单轮 ✅ | 单轮对话的用户消息 |
| `messages` | 多轮 ✅ | 多轮对话的消息列表（按顺序发送） |
| `tags` | ❌ | 分类标签，便于筛选 |
| `expected` | ❌ | 预期结果（当前仅记录，未做自动断言） |

---

## 输出结构

每次运行产出三类 JSON 文件：

```
outputs/bench_test/
├── run_YYYYMMDD_caseid_hash.json    # 单个用例详细日志
├── suite_YYYYMMDD_hash.json         # 套件汇总
└── global_YYYYMMDD_hash.json        # 全局汇总
```

### 关键指标解读

| 指标 | 含义 | 理想值 |
|------|------|--------|
| **iterations** | engine 内循环轮次 | 越少越好（简单任务 ≤ 2） |
| **llm_calls** | LLM API 调用次数 | = iterations |
| **tool_calls** | 工具调用总数 | 按任务复杂度合理即可 |
| **tool_failures** | 工具调用失败数 | 0 |
| **total_tokens** | 总 token 消耗 | 越少越好 |
| **duration_seconds** | 端到端耗时 | 取决于 API 延迟 |
| **status** | 用例状态 | `ok` |

### 质量检查要点

1. **空承诺检测**：查看首轮 LLM 响应的 `content` 字段
   - ✅ `content: ""` + `tool_calls: [...]`（直接行动）
   - ❌ `content: "请稍等，我来帮你..."` + `tool_calls: null`（空承诺）

2. **路由效率**：`route_mode` 应为 `fallback`（通用）或具体技能名
   - 避免出现 `fallback → select_skill → delegate` 的多跳链路

3. **header_row 准确性**：检查 `read_excel` 参数中的 `header_row` 是否与路由预览一致

---

## 外部 Benchmark 数据集

### SpreadsheetBench (NeurIPS 2024)

存放于 `bench/external/spreadsheetbench/`（已 gitignore）。

| 数据集 | 题数 | 说明 |
|--------|------|------|
| `spreadsheetbench_verified_400/` | 400 | 专家标注精选子集 |
| `all_data_912_v0.1/` | 912 | 全量数据集 |

**数据结构：**
```
spreadsheetbench_verified_400/
├── dataset.json           # 题目元数据列表
└── spreadsheet/{id}/      # 每题一个目录
    ├── prompt.txt         # 原始论坛提问
    ├── *_init.xlsx        # 初始 Excel 文件
    └── *_golden.xlsx      # 标准答案 Excel
```

**题目类型分布：**
- Cell-Level Manipulation（公式/提取/计算）：275 题 (verified) / 561 题 (full)
- Sheet-Level Manipulation（跨表/批量/布局）：125 题 (verified) / 351 题 (full)

**来源：** 真实 Excel 论坛问题（excelforum.com），OJ 风格评测。
- GitHub: https://github.com/RUCKBReasoning/SpreadsheetBench
- HuggingFace: https://huggingface.co/datasets/KAKA22/SpreadsheetBench

### 其他已知 Benchmark

| 名称 | 规模 | 特点 | 链接 |
|------|------|------|------|
| **SheetBench-50** | 50 题 | 金融分析、企业级任务 | [hud.ai](https://www.hud.ai/case-studies/sheetbench-50) |
| **AI Spreadsheet Benchmark** | 53 题 | GDP 数据集、产品横评 | [HuggingFace](https://huggingface.co/datasets/rowshq/aispreadsheetbenchmark) |
| **TableBench** | — | 表格 QA、数值推理 | [tablebench.github.io](https://tablebench.github.io/) |

---

## 环境变量

运行 bench 时可通过环境变量控制行为：

| 变量 | 值 | 说明 |
|------|-----|------|
| `EXCELMANUS_WINDOW_PERCEPTION_ENABLED` | `0` / `1` | 窗口感知开关 |
| `EXCELMANUS_WINDOW_RETURN_MODE` | `enriched` / `anchored` | 窗口返回模式 |
| `EXCELMANUS_BENCH_DISABLE_PLAN_INTERCEPT` | `1` | 跳过计划拦截门禁 |

---

## 编写新套件

1. 在 `bench/cases/` 下创建 `suite_XX_描述.json`
2. 遵循上方用例格式，确保 `id` 全局唯一
3. 测试文件放在 `examples/bench/` 下
4. 先用 `--suite` 单独跑确认无异常，再合入全量

**命名规范：**
- `suite_01` ~ `suite_14`：已被占用（功能/样式系列）
- `suite_15`：多轮对话
- `suite_basic`：冒烟测试
- `suite_window_*`：窗口感知专项
- `suite_ab_*`：A/B 对比专项
