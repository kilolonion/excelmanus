# ExcelManus — 产品概要

ExcelManus v4 是一个基于大语言模型的 Excel 智能代理框架。

核心架构为 **Tools + Skillpacks 双层**：
- **Tools 层**：基础能力执行（工具函数 + JSON schema + 安全边界）
- **Skillpacks 层**：策略编排（`SKILL.md` 元数据驱动路由 + `allowed-tools` 授权）

运行模式：
- CLI 模式：终端交互，支持斜杠命令（`/skills`、`/plan`、`/subagent`、`/fullAccess` 等）
- API 模式：FastAPI REST 服务，SSE 事件流

关键能力：
- Excel 读写、分析、图表、格式化、跨表操作
- MCP（Model Context Protocol）客户端集成
- Subagent 委派（大文件处理等场景）
- 窗口感知层（Window Perception）：管理多 Excel 视口的上下文预算
- Accept 门禁与审计：破坏性操作需用户确认，自动落盘审计产物
- 持久记忆系统：跨会话记忆主题文件
- Plan 模式：LLM 生成执行计划，用户审批后执行

项目语言：中文为主（文档、注释、提交信息），代码标识符使用英文。
