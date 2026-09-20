# ExcelManus Web

ExcelManus 的 Web 工作台，基于 Next.js 16、React 19、Tailwind CSS 4 和 Univer 构建。适用版本：1.8.0 源码；更新日期：2026-09-19。

[项目首页](../README.md) · [文档导航](../docs/README.md) · [服务器部署](../docs/ops-manual.md)

## 本地开发

需要 Node.js ≥ 20.9。先按项目首页启动后端，再在本目录运行：

```bash
npm ci
npm run dev
```

默认访问 [http://localhost:3000](http://localhost:3000)，后端默认地址为 `http://127.0.0.1:8000`。开发和生产前端默认只绑定 `127.0.0.1`。前端会代理 API，因此远程访问前端也等同于开放后端；对外部署须设置管理令牌并使用受控反向代理，详见[运维手册](../docs/ops-manual.md)。

模型和产品设置通过设置页写入后端主数据库。`web/.env.local` 只用于前端服务参数，不用于保存模型 API Key。

## 后端地址

| 参数 | 读取时机 | 用途 |
| --- | --- | --- |
| `EXCELMANUS_RUNTIME_BACKEND_ORIGIN` | Next.js 运行时 | 浏览器使用的后端地址；`same-origin` 表示同源访问 |
| `NEXT_PUBLIC_BACKEND_ORIGIN` | 构建时 | 未设置运行时地址时的兼容回退；修改后需要重新构建 |
| `BACKEND_INTERNAL_URL` | Next.js 配置与构建 | `/api/v1/*` rewrite 的代理目标，默认 `http://127.0.0.1:8000` |
| `PORT` | 服务启动时 | 前端监听端口 |

浏览器地址解析优先级：运行时地址 → 构建时地址 → 本地 HTTP 页面使用当前主机的 8000 端口 → 其他场景默认同源。HTTPS 页面不会直接连接 HTTP 后端。解析逻辑位于 `src/lib/backend-origin.ts`；API 调用共用 `src/lib/api.ts`。

有可用直连地址时，当前浏览器 API 请求使用该地址；同源模式下使用相对 `/api/v1` 路径。因此更换后端端口时，应同时检查运行时地址与 rewrite 配置。

本地后端使用 9000 端口的 `web/.env.local` 示例：

```dotenv
EXCELMANUS_RUNTIME_BACKEND_ORIGIN=http://127.0.0.1:9000
BACKEND_INTERNAL_URL=http://127.0.0.1:9000
```

Nginx 同源部署示例：

```dotenv
EXCELMANUS_RUNTIME_BACKEND_ORIGIN=same-origin
BACKEND_INTERNAL_URL=http://127.0.0.1:8000
```

反向代理需要关闭 API 事件流缓冲，详见 [运维手册](../docs/ops-manual.md)。桌面版由启动器注入动态端口，不应将本机固定端口写入桌面构建。

## 生产构建

在 `web/` 目录运行：

```bash
npm ci
npm run build
npm run start
```

当前 `build` 和 `build:webpack` 都使用 webpack。构建前会从 `../pyproject.toml` 同步包版本；类型错误不会被构建配置忽略。

需要独立部署 Next.js standalone 产物时，同时复制静态资源：

```bash
mkdir -p .next/standalone/.next
cp -R public .next/standalone/
cp -R .next/static .next/standalone/.next/
node .next/standalone/server.js
```

`.next/standalone/`、`.next/static/` 和 `public/` 应来自同一次构建。跨机器发布时，确认操作系统、架构和原生依赖兼容。源码启动脚本与桌面打包脚本已有各自的产物准备流程。

## 检查

```bash
npx tsc --noEmit -p .
npm test
npm run lint
```

按修改范围选择相关检查。`src/__tests__/` 包含会话状态、事件流、文件视图、表格编辑和引导流程等测试。隐私政策与服务协议有对应的网页正文，更新 `docs/privacy-policy.md` 或 `docs/terms-of-service.md` 时，应同步 `src/app/privacy/page.tsx`、`src/app/terms/page.tsx`。

## 主要目录

```text
src/
├── app/          # 页面、布局、隐私与服务说明
├── components/   # 聊天、工作台、设置、文件和表格组件
├── hooks/        # 可复用交互逻辑
├── lib/          # API、事件处理、工作簿与运行时配置
├── stores/       # Zustand 状态
└── __tests__/    # 前端测试
```
