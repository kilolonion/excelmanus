FROM node:20-alpine AS base

WORKDIR /app

# Dependencies
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts

# Build
# NEXT_PUBLIC_BACKEND_ORIGIN 在 next build 时内联到客户端 JS 中：
#   - 留空（默认）→ 前端自动回退 http://{hostname}:8000 直连后端
#   - "same-origin" → 走同源（Nginx 反向代理 /api/ 且关闭 buffering 时使用）
ARG NEXT_PUBLIC_BACKEND_ORIGIN=""
ENV NEXT_PUBLIC_BACKEND_ORIGIN=${NEXT_PUBLIC_BACKEND_ORIGIN}
COPY . .
RUN npm run build

# Production image
FROM node:20-alpine AS runner
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

CMD ["node", "server.js"]
