# 权限、工作区与代码执行

## 模式与权限 {#permissions}

当前权限以[运行状态](knowledge:runtime)和[授权工具目录](knowledge:tools)为准。read/plan 会收窄写能力，子代理在父权限内再收窄。能力发现和文档阅读不授予工具、文件或网络权限。审批是否需要确认由当前执行策略及动作决定，工具分类标签不是执行许可。

## 工作区与源码隔离 {#workspace}

文件工具受工作区、保留目录、敏感文件和源码隔离限制。uploads 是只读输入，outputs 用于交付。即使工作区恰好是产品源码仓库，也不能假定 docs 或代码路径可读。统一门户通过随包发布的文档 ID 提供必要说明，不要求读取源码，不接受任意文件路径，不通过文件或 shell 绕开隔离。

## Python SDK {#sdk}

单次业务操作直接调用对应工具。循环、跨工具组合、自定义计算可用[run_code](knowledge:tool:run_code)。其 Python 环境用 import em 访问授权目录里的工具；通过[工具详情](knowledge:tools)查询函数签名、参数及输出。工作区业务写入经过 em 工具和提交机制；不要用 Workbook.save、to_excel 或 ExcelWriter 绕过版本与提交约束。

## 并发与写入顺序 {#ordering}

独立只读调用可按宿主配置并发；有先后依赖的操作顺序执行，写入串行。程序 stdout 不证明业务正确。失败后核对哪些操作已经提交，不重放已经完成或结果不确定的写入。

## 运行依赖与执行限制 {#environment}

运行环境查询只报告探测事实。Office、PDF 引擎存在不保证能启动或成功处理当前文件；Docker 模式的宿主探测不代表容器内部能力。run_code 的子进程、网络和资源限制由当前隔离与审批策略决定；普通工具和代码执行的许可不能互相推导。

## 相关主题 {#next}

继续阅读：[恢复策略](knowledge:doc:recovery)、[配置范围](knowledge:doc:configuration)、[权限错误](knowledge:error:PERMISSION_DENIED)、[源码隔离错误](knowledge:error:PRODUCT_SOURCE_FORBIDDEN)。
