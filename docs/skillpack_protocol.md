# Skillpack 协议规范（SSOT）

> 最后更新：2026-02-14  
> 适用范围：`excelmanus/skillpacks`、README、测试与任务文档

## 1. 目标
- 统一 Skillpack 的加载协议、路由语义与文档口径。
- 避免“实现已变更、文档未同步”造成的认知漂移。

## 2. 三层加载与覆盖
- 覆盖优先级：`system < user < project`。
- 同名 Skillpack 以高优先级来源覆盖低优先级来源。
- system 仅作为内置默认集；project 可覆盖 system/user。

## 3. 目录发现规则
发现顺序（低优先级到高优先级）：
1. system：`excelmanus/skillpacks/system`
2. user：`~/.excelmanus/skillpacks`、`~/.claude/skills`、`~/.openclaw/skills`
3. project：
   - 祖先链：`cwd -> workspace_root` 逐层 `.agents/skills`
   - 显式目录：`.excelmanus/skillpacks`、`.agents/skills`、`.claude/skills`、`.openclaw/skills`

严格协议说明：
- 项目级 OpenClaw 目录仅支持 `.openclaw/skills`。
- `workspace/skills` 不再作为 OpenClaw 项目级目录。

## 4. 路由语义
- 斜杠命令：`/<skill_name> args...` 直连技能（`slash_direct`）。
- 非斜杠消息：进入 `fallback`，注入 `list_skills` 与只读发现工具。
- 兜底模式不注入技能目录正文到 `system_contexts`，目录通过 `select_skill` 元工具描述传递。

## 5. 内置 system Skillpacks（权威清单）
- `general_excel`
- `data_basic`
- `chart_basic`
- `format_basic`
- `file_ops`
- `sheet_ops`
- `excel_code_runner`

## 6. 变更治理要求
- 协议变更必须同时更新：实现、README、测试、本文档。
- 新增/删除内置 system Skillpack 时，必须同步：
  - `excelmanus/skillpacks/system/*`
  - README 内置清单
  - 契约测试 `tests/test_skillpack_docs_contract.py`
