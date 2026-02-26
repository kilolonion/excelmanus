# ExcelManus Web

ExcelManus 的前端项目，基于 Next.js 16 + React 19 + Tailwind CSS 4 + shadcn/ui 构建。

## 技术栈

- **框架**: Next.js 16 (App Router, standalone output)
- **UI**: Tailwind CSS 4, shadcn/ui (Radix UI), Lucide React, Framer Motion
- **状态管理**: Zustand
- **Excel 预览**: Univer Sheets
- **Markdown**: react-markdown + remark-gfm + highlight.js

## 本地开发

```bash
npm install
npm run dev
```

浏览器访问 [http://localhost:3000](http://localhost:3000)。

默认通过 `next.config.ts` 中的 rewrite 代理 `/api/v1/*` 请求到后端 `http://localhost:8000`，可通过 `BACKEND_INTERNAL_URL` 环境变量覆盖。

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `BACKEND_INTERNAL_URL` | Next.js 服务端 rewrite 代理目标地址 | `http://localhost:8000` |
| `NEXT_PUBLIC_BACKEND_ORIGIN` | 客户端直连后端地址（构建时内联）。留空自动回退 `http://{hostname}:8000`；设为 `same-origin` 走 Nginx 反代 | 空 |

## 构建与部署

```bash
npm run build
```

构建产物位于 `.next/standalone/`（standalone 模式）。部署时需手动复制静态资源：

```bash
cp -r public .next/standalone/
cp -r .next/static .next/standalone/.next/
node .next/standalone/server.js
```

也可使用 Docker：

```bash
docker build -t excelmanus-web .
docker run -p 3000:3000 excelmanus-web
```

## 目录结构

```
src/
├── app/          # Next.js App Router 页面
├── components/   # UI 组件
├── hooks/        # 自定义 React Hooks
├── lib/          # 工具函数与 API 调用
├── stores/       # Zustand 状态 store
└── types/        # TypeScript 类型定义
```
