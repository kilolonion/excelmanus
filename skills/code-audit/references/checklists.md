# Code Audit Checklists

## 使用说明

- 先执行 Layer 1 获取原始信号，再进入 Layer 2 和 Layer 3 做人工确认。
- 如果某条命令在当前仓库不可用，记录“未执行”与原因，不要静默跳过。
- 每条最终 finding 必须包含文件路径、行号、证据片段和修复建议。

## Layer 1：自动化扫描（全仓）

### 1. 项目与技术栈识别

- 文件清单：`rg --files`
- 包管理与配置：`rg -n --glob "*{package.json,pyproject.toml,requirements*.txt,Cargo.toml,go.mod}" "^" .`

### 2. 类型检查

- Python：`pyright` 或 `mypy .`
- TypeScript：`npx tsc --noEmit`
- 只执行仓库中实际存在的命令，避免无关失败噪音。

### 3. Lint

- Python：`ruff check .`
- JavaScript/TypeScript：`npx eslint .`

### 4. 安全敏感词与高风险模式

- 密钥与凭据：`rg -n -S "(?i)(api[_-]?key|secret|password|token|private[_-]?key|access[_-]?key)" .`
- 私钥头：`rg -n -S "BEGIN (RSA|OPENSSH|EC) PRIVATE KEY" .`
- 危险执行：`rg -n -S "(eval\\(|exec\\(|subprocess\\.|shell=True|Runtime\\.getRuntime\\(\\)\\.exec)" .`
- 路径穿越信号：`rg -n -S "(\\.\\./|path\\.join\\(|os\\.path\\.join\\()" .`

### 5. 历史残余与未完成代码

- 注释标记：`rg -n -S "(TODO|FIXME|HACK|TEMP|XXX)" .`
- 占位实现：`rg -n -S "(NotImplementedError|pass\\s*$|throw new Error\\(['\\\"]TODO)" .`

### 6. 类型逃逸与静态检查豁免

- Python：`rg -n -S "(type:\\s*ignore|noqa)" .`
- TypeScript：`rg -n -S "(@ts-ignore|as any|eslint-disable)" .`

### 7. 死代码初筛

- 未使用导入（Python）：`ruff check . --select F401`
- 不可达代码信号：`rg -n -S "(return .*\\n\\s+\\S|if False:|if 0:)" .`

### 8. 依赖循环初筛

- JS/TS（若有 madge）：`npx madge --circular src`
- Python（若有 pydeps）：`pydeps . --show-cycles`
- 无工具时，记录 import 图可疑路径供 Layer 3 复核。

## Layer 2：逐模块深入（单模块）

### 1. 模块入口与主路径

- 识别入口文件、核心服务、数据访问层、接口层。
- 建立调用主链：入口 -> 业务逻辑 -> 外部 I/O -> 返回路径。

### 2. Bug（B）

- 检查空值、边界值、异常路径和默认分支。
- 对关键分支确认“失败时行为”是否可预期。

### 3. Concurrency（C）

- 检查 async 调用是否遗漏 `await`。
- 检查共享可变状态在并发场景下是否被保护。
- 检查阻塞调用是否出现在事件循环关键路径。

### 4. Security（S）

- 检查输入校验、鉴权、权限边界。
- 检查命令执行、SQL 拼接、路径拼接、反序列化风险。
- 检查日志是否泄露敏感字段。

### 5. Architecture（A）

- 检查模块职责是否单一。
- 检查依赖方向是否逆流（基础层依赖上层）。
- 检查是否存在跨层穿透和 God class。

### 6. Performance（P）

- 检查循环内 I/O、重复计算、低效序列化与深拷贝。
- 检查缓存是否有上限、失效策略与生命周期。

### 7. Inconsistency（I）

- 检查同一概念的命名、类型、错误码、时间单位是否一致。
- 检查对外契约字段的可选性和默认值是否一致。

### 8. Remnant（R）与 YAGNI（Y）

- 标记无调用者公共接口、过时注释、实验分支残留。
- 区分“暂未使用但已规划”与“明确冗余可删”。

### 9. Unfinished（U）

- 聚焦 TODO、占位 API、未实现异常分支、空 catch。
- 判断是否会影响线上行为或维护成本。

### 10. Frontend（F）

- 检查 hooks 依赖数组是否正确。
- 检查状态归属是否导致不必要重渲染。
- 检查 SSR/水合、无障碍语义与键盘可访问性。

## Layer 3：跨模块一致性

### 1. API 契约

- 对齐请求参数、响应字段、错误码、分页和时间格式。

### 2. 配置一致性

- 对齐 `.env.example`、README、默认配置、部署脚本。

### 3. 错误处理模式

- 对齐重试策略、超时策略、降级行为和日志字段。

### 4. 依赖与版本

- 对齐共享依赖版本，识别重复或冲突依赖。

### 5. 命名与语义

- 对齐同义词、缩写与领域术语，避免跨模块误解。

## Finding 输出模板

```markdown
#### {编号}: {一句话标题}
- **文件**: `path/to/file.py:42-58`
- **严重度**: 🔴高 / 🟠中 / 🟡低
- **证据**:
  ```python
  # 问题代码片段
  ```
- **问题**: {具体描述与风险}
- **建议修复**: {修复方向或代码示例}
```
