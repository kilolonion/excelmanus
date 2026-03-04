FROM node:22-alpine AS base

WORKDIR /app

# Dependencies
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts

# Build
# NEXT_PUBLIC_BACKEND_ORIGIN 在 next build 时内联到客户端 JS 中（构建时固化，向后兼容）：
#   - 留空（默认）→ 前端自动回退 http://{hostname}:8000 直连后端
#   - "same-origin" → 走同源（Nginx 反向代理 /api/ 且关闭 buffering 时使用）
# 推荐改用运行时变量 EXCELMANUS_RUNTIME_BACKEND_ORIGIN（见下方），无需重新构建。
ARG NEXT_PUBLIC_BACKEND_ORIGIN=""
ENV NEXT_PUBLIC_BACKEND_ORIGIN=${NEXT_PUBLIC_BACKEND_ORIGIN}
COPY . .
RUN npm run build

# Production image
FROM node:22-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

RUN addgroup --system --gid 1001 nodejs && \
    adduser --system --uid 1001 nextjs

COPY --from=base /app/public ./public
COPY --from=base --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=base --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs

EXPOSE 3000

ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

# 运行时后端地址（优先级高于构建时的 NEXT_PUBLIC_BACKEND_ORIGIN）：
#   - 留空（默认）→ 回退到 NEXT_PUBLIC_BACKEND_ORIGIN 或 http://{hostname}:8000
#   - "same-origin" → 走同源（Nginx 反代场景推荐）
#   - "http://backend:8000" → 直连指定后端
#
# ★ 无需重新构建镜像 ★ — 通过 RuntimeConfigScript (Server Component) 在 HTML 中
# 注入 window.__EXCELMANUS_RUNTIME__，客户端自动优先读取运行时值。
#
# 用法示例：
#   docker run -e EXCELMANUS_RUNTIME_BACKEND_ORIGIN=same-origin ...
#   docker compose: environment: - EXCELMANUS_RUNTIME_BACKEND_ORIGIN=http://backend:8000
# ENV EXCELMANUS_RUNTIME_BACKEND_ORIGIN=""

CMD ["node", "server.js"]
