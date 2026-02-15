# P 规划：窗口感知层 Phase 2

## 工作分解（WBS）
1. 配置扩展：新增顾问模式、超时、触发阈值、TTL。
2. 生命周期模型扩展：`LifecyclePlan` 增加 `task_type/generated_turn`。
3. 顾问实现：新增 `HybridAdvisor`，实现小模型计划覆盖与合法性校验。
4. 小模型协议：新增 `small_model.py`，提供提示词构造与 JSON 解析。
5. 管理器改造：异步触发、缓存计划、单飞并发控制、reset 清理。
6. 引擎接入：绑定异步 runner，复用 router 模型调用，注入轮次提示。
7. 文档更新：README 新增窗口感知顾问配置说明。
8. 测试补齐：新增 small_model 测试，扩展 advisor/budget/config/engine 测试。

## 验收标准（DoD）
- `EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_*` 配置可解析且有测试覆盖。
- `hybrid` 模式下：小模型成功时可影响下一轮窗口渲染，失败时回退规则。
- `build_system_notice()` 保持同步，不等待小模型返回。
- `reset()/clear_memory()` 后无遗留 advisor 协程。
- 窗口感知相关回归测试全部通过。

## 依赖与风险
- 依赖：Router 模型通道可用。
- 风险：模型返回非 JSON，需解析回退。
- 风险：触发条件过于频繁，需单飞限制避免并发堆积。
