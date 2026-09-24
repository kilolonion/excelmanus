# 计划、任务与子代理

## 计划与任务 {#plan}

复杂任务可分解为依赖明确、可验收的步骤。task_create/task_update 记录任务状态，不会自动执行业务。plan 模式先用只读证据确定目标、范围、依赖及验收方式；write_plan 可生成计划，exit_plan_mode 提交计划审批。业务写权限仍由模式和审批策略决定。

## 委派与独立上下文 {#delegate}

使用[子代理列表](knowledge:tool:list_subagents)了解可委派角色，通过[delegate](knowledge:tool:delegate)交付自包含任务。子代理不继承完整主对话，需要提供目标、必要文件、已知证据、限制和返回要求。子代理拥有自己的上下文、模型和权限交集，不能通过主会话门户读到主会话私有状态。

## 后台任务生命周期 {#background}

delegate 默认等待结果；background=true 启动后台任务并返回 run_id，启动成功不代表完成。后续用 status/list/wait 读取状态和最终结果；send 追加信息；pause/cancel 停止；resume 基于已保存历史产生新运行 ID。实际可用 action、返回结构和参数以[delegate 详情](knowledge:tool:delegate)为准。

## 依赖与结果核验 {#dependencies}

只并行执行独立工作。有输入依赖的步骤先等上游结果，多个写入顺序执行。主代理整合子代理结果并验证交付，不能将子代理摘要直接当作已验证的文件修改证据。

## 用户消息投递 {#dispatch}

用户在运行中追加消息可有 steer、interrupt、queue 等宿主投递方式：补充当前任务、请求中断后接入、或等待当前任务结束。投递回执与模型请求是不同层次；收到新目标时依据实际投递和当前执行状态处理，取消不代表已提交改动被回滚。

## 相关主题 {#next}

继续阅读：[运行状态](knowledge:runtime)、[计划写入](knowledge:tool:write_plan)、[结果恢复](knowledge:doc:recovery)、[上下文](knowledge:doc:context)。
