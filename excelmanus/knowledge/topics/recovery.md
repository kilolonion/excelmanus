# 错误恢复与结果解释

## 结果状态 {#status}

成功、部分成功、已提交后失败、未执行、审批中、取消和未验证应明确区分。工具返回中的 status、error_code、failure_class、remediation、提交回执、版本和覆盖说明共同决定后续动作。不要只凭一段 stdout 或“调用完成”判断文件已经正确改变。

## 参数错误 {#arguments}

参数或字段错误：从[工具目录](knowledge:tools)读取当前 schema 或 tool:name.field，再改参数；不要重复相同错误调用。工具存在也不保证支持任意对象和参数组合，具体限制以合同和实际返回为准。

## 版本冲突 {#versions}

版本与选区失效：遇到[VERSION_CONFLICT](knowledge:error:VERSION_CONFLICT)或[STALE_READ](knowledge:error:STALE_READ)，重新观察受影响范围，用新事实重新决定变更。不能仅替换版本号重放旧写入。

## 权限与审批拒绝 {#permissions}

审批或权限拒绝：说明当前约束和可做的替代步骤，必要时让用户通过宿主设置改变权限。不得用代码、shell、技能或子代理绕过拒绝。计划模式下可以查询本门户，不能通过查询启用写入。

## 覆盖与分页 {#coverage}

部分覆盖：sample、截断、分页、spill、缓存或不支持的区域不能据此推断为全量数据。沿返回的 next_call 或正式 spill 引用继续读取，同一分析固定版本。

## 已提交后失败 {#commits}

已提交后失败或结果不确定：先读实际文件版本与[版本历史](knowledge:tool:manage_spreadsheet_versions)，确认已发生的改动，再决定是否继续。取消、中断或超时不自动撤销提交；不要整段重放可能已提交的写入。

## 错误码目录 {#codes}

用[错误目录](knowledge:errors)或 error:错误码查询当前运行时共用的恢复说明。未知错误码明确返回未收录，保留实际错误信息并回到[运行状态](knowledge:runtime)和[配置](knowledge:settings)定位条件。
