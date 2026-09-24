# 配置来源与生效范围

## 配置来源 {#sources}

产品持久设置以主数据库为准：模型档案与其他运行选项由设置页、配置导入等宿主入口管理。运行期间还可能有内存覆盖和会话级修改。因此磁盘默认值、历史对话、环境变量名不能代表当前会话的生效值。

## 查询实际生效值 {#live}

使用[当前设置](knowledge:settings)读取允许公开的实际值、字段 schema 和生效范围；使用[运行状态](knowledge:runtime)查看当前模型、视觉状态、会话模式及授权边界。未提供的状态返回 unknown/unavailable，不从模型名称猜测。凭证、认证地址、命令及模型档案不通过门户披露。

## 查询与修改条件 {#self-management}

认知查询是只读操作，不要求打开自我管理。配置修改另走 inspect_agent/configure_agent：Agent 自我管理默认启用；开关开启时先加载对应技能，再 inspect 并按返回 schema 修改。主会话、当前权限、write 模式和开关都必须满足。知识门户查询不会自动启用这个开关、修改配置或扩大权限。

## 生效范围 {#effective}

自我管理只改变当前内存会话；重建会话恢复宿主设置。max_iterations 从下一轮用户请求生效，其他可写项供后续调用使用；已运行操作不会被追溯修改，本轮时间、token、费用预算也不因此重置。设置列表中的 session_configurable 表示字段支持会话修改，can_modify_now 表示当前还满足修改条件。

## 常用配置 {#fields}

常见查询：[迭代上限](knowledge:setting:max_iterations)、[上下文容量](knowledge:setting:max_context_tokens)、[只读并发](knowledge:setting:parallel_readonly_tools)、[自我管理开关](knowledge:setting:agent_self_management_enabled)。其他字段从设置目录继续读取。

## 宿主与容器 {#host}

主机依赖与执行隔离可能由启动配置管理。宿主安装了程序不代表容器内可执行；[执行边界](knowledge:doc:execution)和实际工具返回共同决定可用性。
