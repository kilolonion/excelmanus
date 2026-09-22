# 工具设计与能力审计

审计日期：2026-09-22  
范围：工具注册与目录、审批/审计、执行器、MCP、Excel/Word 工具、文件格式转换、引用图、评测与发布文档。  
说明：本报告先于 P0 修复落盘；工作区已有未提交的前端、部署和会话改动不属于本次审计改动。

## 结论

当前系统已经具备路径边界、版本校验、审批、事务日志、结果截断和恢复等基础治理能力。主要问题在于，工具的副作用声明、审批路由、审计快照、撤销语义和外部一致性仍由多套规则分别维护；同时，Excel 公式计算、依赖重写和富对象保真度不足以支撑“Excel 等价”承诺。

## P0：需要先修复的运行时设计问题

### 1. 副作用声明不是审批和审计的单一事实源

- `ToolDef.write_effect` 的注释明确说明它不参与审批/审计策略判定。
- `memory_save` 声明为 `external_write`，但没有进入标准审计/审批集合。
- `manage_skills` 声明为 `workspace_write`，其 `install/uninstall` 由专用 handler 直接执行，绕过通用审批处理器。
- 动态工具和未知工具由不同路径处理，规则可能继续漂移。

影响：持久记忆、技能安装/卸载以及扩展工具的执行记录、用户确认和撤销行为不一致。

目标：建立统一的工具能力描述，至少包含 `write_effect`、按 action 的副作用、审批级别、审计策略、撤销能力、幂等键、超时/取消能力和一致性等级；目录、路由、执行器、审计和重放缓存均从同一描述派生。

### 2. MCP 不能依赖生态增加字段，但当前默认策略仍不够安全

MCP 工具目前通过名称、描述、schema 字段和 scope 的关键词推断副作用，并将工具标为 `external_unverified`。这可以保持生态兼容，但不能把未知工具直接当作普通只读调用。

目标：不要求 MCP 增加字段；在宿主侧采用安全默认值：未知副作用默认独占、不可自动重放、必须产生外部操作回执；明确命中写入语义的工具进入统一外部写入路径；失败或取消后返回“需 provider 查询”的状态，而不是自动重试。对于已明确只读的工具，仍允许正常读取和短时缓存。

### 3. 取消和超时无法保证硬终止

普通同步工具在线程池执行，Python 线程无法被 `Task.cancel()` 终止；取消路径会持续等待线程和子调用排空。代码执行虽然使用本机子进程和 wrapper，但不是操作系统级隔离；Windows 不具备 Unix `resource` 限制。

影响：工具卡死时，任务取消、会话停止和执行槽释放可能超过预期；本机代码执行不能作为强安全边界。

目标：将不可协作取消的重型/动态工具放入可终止的 worker process；Unix 使用 process group，Windows 使用 Job Object；超时杀死进程树并写入确定的终止结果；保留版本和事务围栏，避免旧进程在取消后提交文件。

## P1：功能正确性和覆盖缺口

1. 没有公式重新计算闭环；未缓存公式只能返回公式文本，比较明确采用“文本比较，不重新计算”。跨文件公式计算未实现。
2. 结构修改对整个工作簿扫描，任何公式、图表、表对象或名称都可能阻止无关区域的插入/删除；数据清洗遇到公式、合并、条件格式或验证时整体拒绝。
3. 公式引用解析主要依赖正则，缺少结构化引用、动态引用、数组溢出、三维引用和高效依赖索引；依赖查询会重复扫描单元格。
4. 连接只支持左连接和单列键；透视结果是静态矩阵，不是可刷新的 PivotTable；跨文件原子事务未实现。
5. 富对象工具当前主要是创建少数类型的图表，不能完整更新或删除已有对象；`.xls/.xlsb` 转换会丢失大量对象和格式。
6. Word 写入集中在段落、表格和模板填充，页眉页脚、合并表格、评论、修订、复杂版式和嵌入对象支持不足。
7. 统一 range 语法在读、写、格式化和 Word 场景中的可执行子集不同，导致同一地址在不同工具中表现不一致。

## P2：体验、评测和交付缺口

1. 当前有 33 个内置工具，表格意图工具 schema 很大，目录会剥离深层描述并要求额外 introspection；模型容易产生额外发现调用和参数错误。
2. 同一轮只读结果按工具名、参数和文件版本缓存；外部搜索/只读 MCP 没有 TTL 或 provider epoch，可能得到过期结果。
3. 输出合同主要校验顶层字段，外部工具可以保持未知；大结果虽有 spill，但不是所有工具都有一致的分页/继续协议。
4. benchmark 的迭代、调用数、失败数和耗时默认只告警，不形成发布失败门禁；发布说明中的工具数量和历史测试数字可能落后于运行时。
5. 产品明确是单用户、进程级会话/缓存，不提供多租户隔离；多 worker、Docker 隔离和无中断升级不是当前能力。

## 当前验证证据

本机 Windows 执行 `python -m pytest -q --disable-warnings --maxfail=20` 时，在停止点得到：2085 passed、9 failed、11 errors、5 skipped。已确认的失败类别：

- 新建文本文件的两个 `/undo` 用例失败，说明创建动作没有可用的补偿撤销。
- `run_code` 用例因显式解释器的 pandas 依赖探测失败；失败结果还会在 60 秒内缓存。
- API 字段和事件 dataclass 用例反映接口/测试合同漂移。
- interaction recovery 用例在 fixture setup 阶段触发 pytest/pytest-asyncio/pytest-qt 兼容性错误。

## P0 完成标准

- 所有内置工具和 action 的副作用、审批、审计和一致性判定来自统一能力描述。
- `memory_save`、`manage_skills` 的持久写入不再绕过标准执行记录；只读 action 不被误拦截。
- 不要求 MCP 改 schema；未知/外部写入走安全默认、独占、回执、不可重放路径。
- 取消和超时对动态/重型工具有明确的硬终止结果，不会无限排空。
- 新建文件、修改文件和外部操作分别有明确的撤销/补偿语义。
- 相关单元测试、回归测试和跨平台行为说明同步更新。

## P0 实施记录（2026-09-22）

### 继续复核发现（修复前记录）

- 前一版审批回调在工作线程使用 `asyncio.run`，会将 MCP client 带到另一个 event loop；应回到原宿主 loop 执行，并把取消转为可审计的结果。
- 工具能力只在初始化时绑定快照，后续注册/替换可遗漏；当前工作区已加入 `ToolCapability`，需要贯通 action 判定和动态 registry 查询，并保留会话审批覆盖。
- 前一版 `cancel_call` 同步等待 `taskkill`，会阻塞主事件循环；无 execution id 的超时仅杀父进程，Unix SIGTERM 后也未可靠升级为整个进程组 SIGKILL。
- 前一版取消测试只是检查返回 `CANCELLED`，没有证实孙进程退出或 pending 文件未发布；需要实际进程和迟到写入验证。
- 前一版关于“全部完成”“既有失败”的表述证据不足：未对所有失败做基线对照，普通同步线程仍无硬终止，Windows Job Object 尚未实现。以下最终验证记录应覆盖此前的进度性结论。

### 继续复核后的处理结果

- 批准执行改用 `run_coroutine_threadsafe` 回到宿主 event loop，MCP async client 不再跨 loop。
- `cancel_call` 只在 event loop 中标记取消，进程树终止移到 daemon worker；现有 Windows desktop Job Object 负责宿主树生命周期，工具级取消使用 `taskkill /T`，Unix 使用进程组终止并升级 kill。
- 新增后代进程回归：脚本启动的 child PID 在取消后退出；新增 MCP async loop 回归确认 approved path 仍使用原 loop。
- 旧 `ToolDef` 未声明 `write_effect` 时，宿主会尊重会话级 confirm/audit 覆盖并恢复本地文件工具的快照/undo 语义；MCP 绑定到 registry 后 unknown/external 按 fail-closed confirm 处理，不要求 provider 新字段。
- 普通不可协作同步函数仍只能排空等待，未伪称为可硬杀；这仍是 P1 的隔离升级边界。

审计文档在运行时代码修改前已创建并落盘。随后按上述边界完成了以下宿主侧修复：

- `ApprovalManager` 绑定当前 registry 的 `ToolDef` 快照，按现有 `write_effect`、`actions` 和 MCP 宿主启发式推导审批、审计、撤销能力；没有增加任何 MCP provider 字段。
- 普通调用与 accept 后调用都经过 `ToolDispatcher.call_registry_tool`；MCP 写入沿用现有 `operation_id`/TxLog，并禁用基于 workspace 版本的 MCP replay。专用 `manage_skills` 和 MCP 直通 handler 由 dispatcher 补写统一 manifest/DB 审计。
- `run_code`、`run_shell` 的本机子进程按 execution id 注册。取消和超时会终止进程树并丢弃 pending 发布目录；运行时仍对不可终止的普通同步工具保留排空等待，避免晚到提交。
- 审计撤销支持“before 不存在、after 存在”的新文件：只有当前 after hash 完全匹配时才通过 `WorkspaceFileService` 删除，后续修改会触发版本冲突。

定向验证（仓库 `.venv`）：审批/策略/MCP/撤销/Shell/Code/取消回归 209 项通过、19 项按环境条件跳过；审批流程、dispatcher 和 undo UI 42 项通过；新增后代进程与 MCP event-loop 回归通过，`run_code` 取消实测约 1.2 秒返回 `CANCELLED` 且 pending 写入未发布。仓库全量基线为 5995 passed、43 skipped、8 failed；取消测试中仍有 3 个既有模型 mock/JEV 继续核对用例因响应耗尽失败，未涉及进程终止或审批审计断言。新文件撤销相关旧断言已同步更新为“安全删除新文件”。
