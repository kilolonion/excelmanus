---
name: sandbox_awareness
version: "1.0.0"
priority: 20
layer: strategy
max_tokens: 400
conditions:
  full_access: false
---
## 沙盒安全机制

你的 `run_code` 在安全沙盒中执行，代码会经过 **AST 静态分析** 自动分级：

| 级别 | 触发条件 | 结果 |
|------|----------|------|
| **GREEN** | 仅用 pandas/openpyxl/numpy/math/re 等数据处理库 | 自动执行 |
| **YELLOW** | 导入了网络模块（requests/urllib/httpx/aiohttp 等） | 可自动执行，但网络调用会被运行时拦截 |
| **RED** | 使用了 subprocess/exec()/eval()/ctypes/signal，或语法错误 | 暂停执行，需用户 `/accept` 批准 |

### 运行时限制（即使代码通过分级，以下操作仍会被拦截）

- **文件写入**：只能写入工作区目录和系统临时目录，工作区外路径抛出 `PermissionError`
- **进程创建**：`subprocess.run/Popen/call` 等被禁用，抛出 `RuntimeError`
- **网络连接**：`socket.socket()` 被禁用，无法建立网络连接
- **动态执行**：`exec()`/`eval()` 被禁用（eval 仅允许 `ast.literal_eval` 字面量求值）
- **系统调用**：`os.system()`/`os.popen()` 被禁用
- **退出调用**：`sys.exit()`/`exit()`/`os._exit()` 触发 RED 分级（系统会尝试自动清洗）

### 正确做法

- 编写纯数据处理代码（pandas/openpyxl/numpy），保持 GREEN 级别即可自动执行
- 非必要不要尝试网络请求、进程调用、动态代码执行
- 复制文件用 `copy_file` 工具而非 `shutil.copy`（后者可能被路径保护拦截）
- 遇到 `PermissionError` 或"安全策略禁止"错误时，不要重试同一方案，改用内置工具或纯数据处理方式
