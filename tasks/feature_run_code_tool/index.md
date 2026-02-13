# feature_run_code_tool — 合并代码执行工具 + 受限 Shell

> **状态**：Phase 1 ✅ | Phase 2 ✅ | Phase 3 暂不实施
> **创建**：2025-02-13
> **实际工期**：Phase 1 + Phase 2 同天完成

## 背景

借鉴 Claude Code Bash 工具设计：
1. 将 `run_python_script` 升级为统一的 `run_code`（内联+文件双模式），减少 LLM 调用次数
2. 新增 `run_shell` 受限 Shell 工具（白名单命令模式），提供文件探查和环境信息查询能力

---

## Phase 1：合并 `run_code` 工具 ✅

**核心改动**：`run_python_script` → `run_code`，支持 `code`（内联）和 `script_path`（文件）两种互斥模式。

| 文件 | 改动 |
|------|------|
| `excelmanus/tools/code_tools.py` | 新增 `run_code` + `_execute_script`，删除 `run_python_script` |
| `excelmanus/approval.py` | `HIGH_RISK_TOOLS` 替换 |
| `excelmanus/engine.py` | 3 处引用替换 |
| `excelmanus/subagent/builtin.py` | 3 处工具列表替换 |
| `excelmanus/subagent/executor.py` | 1 处 `undoable` 判断 |
| `excelmanus/skillpacks/system/excel_code_runner/SKILL.md` | `allowed_tools` 替换 |
| `tests/test_code_tools.py` | 重写为 4 个测试类 14 个用例 |
| `tests/test_approval.py` | 2 处引用替换 |
| `README.md` | 2 处文档更新 |

---

## Phase 2：`run_shell` 受限 Shell 工具 ✅

**核心能力**：白名单命令模式，仅允许只读探查命令。

### 安全设计

- **白名单**：`ls`, `cat`, `head`, `tail`, `wc`, `grep`, `find`, `sort`, `uniq`, `cut`, `awk`, `sed`, `echo`, `pwd`, `which`, `env`, `date` 等
- **黑名单**：`rm`, `curl`, `wget`, `sudo`, `bash`, `chmod`, `kill`, `dd`, `apt`, `brew` 等
- **注入防御**：禁止反引号、`$()`、`${}`、`;`、重定向 `>`/`<`
- **子命令限制**：`python` 仅允许 `--version`；`pip` 仅允许 `list/show/freeze`
- **shell=False**：始终以列表方式传参，不经过 shell 解释

### 改动文件

| 文件 | 改动 |
|------|------|
| `excelmanus/tools/shell_tools.py` | **新建**，完整实现 |
| `excelmanus/tools/registry.py` | 注册 `shell_tools` |
| `excelmanus/approval.py` | `HIGH_RISK_TOOLS` 加入 `run_shell` |
| `excelmanus/engine.py` | 2 处 `undoable` 判断 + 权限提示文本 |
| `excelmanus/subagent/builtin.py` | `_ANALYSIS_TOOLS` / `_CODER_TOOLS` 加入 |
| `excelmanus/subagent/executor.py` | `undoable` 判断加入 |
| `tests/test_shell_tools.py` | **新建**，7 个测试类 28 个用例 |

### 测试结果

- `tests/test_shell_tools.py`：28/28 passed
- 全量测试：664 passed（+28），0 新增失败

---

## Phase 3：持久 Shell 会话（暂不实施）

仅记录方向：`PersistentShell` 长活 bash 子进程、状态保持、Docker/VM 沙箱。当前 Excel 场景需求不强。
