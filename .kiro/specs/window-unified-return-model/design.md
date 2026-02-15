# 设计文档：Window Rule Engine v2（WURM v2）

## 1. 设计目标

- 将窗口规则从“分散常量 + 局部启发式”升级为“集中注册表 + 可解释决策”。
- 让 ingest 写入与窗口缓存语义一致，降低错误合并和 stale 误报。
- 让 anchored/unified 确认文本完全协议化，支持 round-trip 解析。
- 保留 `v1` 快速回退能力，避免一次性切换风险。

## 2. 架构总览

### 2.1 核心组件

- `rule_registry.py`
  - `classify_tool_meta(tool_name) -> ToolMeta`
  - `resolve_intent_decision(...) -> IntentDecision`
  - `repeat_threshold(intent) -> (warn, trip)`
- `confirmation.py`
  - `build_confirmation_record(...)`
  - `serialize_confirmation(record, mode)`
  - `parse_confirmation(text)`
- `manager.py`
  - 按 `window_rule_engine_version` 在 v1/v2 之间路由
  - 承担 focus、repeat、lifecycle、render 协调
- `ingest.py`
  - 读取合并与写入 patch 的数据语义落地

### 2.2 版本开关与回退

- 配置项：`window_rule_engine_version`
- 环境变量：`EXCELMANUS_WINDOW_RULE_ENGINE_VERSION`
- 允许值：`v1 | v2`
- 默认值：`v1`

> 设计原则：任何 v2 新行为都必须通过该开关可回退。

## 3. 决策流设计

### 3.1 工具分类

- v2：`manager._classify_tool` 调用 `classify_tool_meta`
- v1：走历史 `classify_tool` 路径
- 输出统一抽象：`canonical_name`、`window_type`、`read_like`、`write_like`、`rule_id`

### 3.2 intent 判定

优先级顺序：

1. 用户语义规则
2. 工具语义规则
3. carry
4. default

约束：

- 粘性锁：锁期内除强制切换外不跨类抖动
- v2 产出 `rule_id`，用于归因和调试日志

### 3.3 repeat 阈值

- 计数键：`(file, sheet, range, intent)`
- 阈值：
  - `aggregate/validate/formula`：基础阈值
  - `format/entry/general`：放宽一档
- 写入后：清空同 `(file, sheet)` 下所有 intent 计数

## 4. ingest 语义设计

### 4.1 读取合并

- 主路径：范围几何 + 行位置
- 非法范围退化：主键合并
- 不再把“整行内容签名去重”作为主路径，避免“同值不同行”误去重

### 4.2 写入一致性

- 当 `target_range` 与缓存可映射时：
  - 必须 patch `cached_ranges` 与 `data_buffer`
  - `stale_hint = None`
- 无法映射时：
  - 不强行猜测 patch
  - 设置 `stale_hint` 提醒潜在影响

### 4.3 schema/columns 兼容

- `schema` 作为 v2 主字段
- `columns` 保留兼容旧渲染路径
- manager 层保证双向同步，至少保留一个版本周期

## 5. confirmation 协议设计

流程：

1. 先构造 `ConfirmationRecord`
2. 再按 `anchored|unified` 序列化
3. 提供 `parse_confirmation` 做回读校验

统一字段：

- 窗口标识
- 操作类型
- 影响范围
- 行列规模
- 变化摘要
- intent
- 重复读取提示（可选）

## 6. focus 语义设计

- 焦点切换：
  - 新窗口激活
  - 旧 active 自动降级为 SUMMARY（预算紧张降 ICON）
- 无效窗口：
  - 返回错误 + `available_windows`
- `clear_filter`：
  - 还原数据后强制 intent=`validate`
  - 刷新 `intent_updated_turn` 与 `intent_lock_until_turn`
- `scroll/expand/refill`：
  - 继承原 intent，不触发重判

## 7. 生命周期可解释性

- `RuleBasedAdvisor` 在 `WindowAdvice` 中增加 `reason_code`
- 高优先意图（`validate/formula`）可将 tier 前移一档，降低过早回收概率
- manager 在 debug 日志输出生命周期原因码，便于 bench 归因

## 8. Bench 可比性设计

- `run_3way_ab.sh`
  - 修复多 suite 参数传递（一次 `--suite` 带多个路径）
  - 注入 `EXCELMANUS_BENCH_DISABLE_PLAN_INTERCEPT=1`
- `analyze_3way.py`
  - 用 `iterations < turn_count` 判定 `invalid_for_perf`
  - 在 case/suite/global 维度显式统计并导出 CSV 字段

## 9. 发布与回滚策略

1. 阶段一：默认 `v1`，CI 并行验证 `v2`
2. 阶段二：灰度切默认 `v2`
3. 异常回滚：`EXCELMANUS_WINDOW_RULE_ENGINE_VERSION=v1`

## 10. 实现状态（2026-02-15）

- ✅ v2 规则注册表与 manager 分支接入完成
- ✅ ingest 合并/写入语义按新规则落地
- ✅ confirmation 协议化与 round-trip 解析完成
- ✅ focus/repeat/advisor 可解释性规则完成
- ✅ bench 传参与无效样本标记完成
- ✅ 新增与调整测试通过（窗口相关与 engine 窗口子集）
