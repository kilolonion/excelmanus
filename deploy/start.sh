#!/usr/bin/env bash
# ExcelManus 一键启动脚本：同时启动 FastAPI 后端 + Next.js 前端

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_ROOT"

GREEN='\033[0;32m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}🚀 ExcelManus 启动中...${NC}"

# 检查 .venv
if [ ! -d ".venv" ]; then
  echo -e "${RED}❌ 未找到 .venv 虚拟环境，请先运行: uv venv && uv pip install -e .${NC}"
  exit 1
fi

# 检查 web/node_modules
if [ ! -d "web/node_modules" ]; then
  echo "📦 首次启动，安装前端依赖..."
  (cd web && npm install) || { echo -e "${RED}❌ npm install 失败${NC}"; exit 1; }
fi

# 清理残留端口（lsof 无结果时返回 1，用 || true 避免退出）
for PORT in 8000 3000; do
  PIDS=$(lsof -ti :"$PORT" 2>/dev/null || true)
  if [ -n "$PIDS" ]; then
    echo -e "${CYAN}⚠ 端口 $PORT 被占用 (PID $PIDS)，正在清理...${NC}"
    echo "$PIDS" | xargs kill -9 2>/dev/null || true
    sleep 1
  fi
done

BACKEND_PID=""
FRONTEND_PID=""

cleanup() {
  echo ""
  echo -e "${CYAN}🛑 正在关闭服务...${NC}"
  [ -n "$FRONTEND_PID" ] && kill "$FRONTEND_PID" 2>/dev/null
  [ -n "$BACKEND_PID" ] && kill "$BACKEND_PID" 2>/dev/null
  wait 2>/dev/null
  echo -e "${GREEN}✅ 已关闭${NC}"
}
trap cleanup EXIT INT TERM

# 启动后端（直接用 uvicorn 启动 ASGI app）
echo -e "${CYAN}▶ 启动 FastAPI 后端 (端口 8000)...${NC}"
.venv/bin/python -c "import uvicorn; uvicorn.run('excelmanus.api:app', host='0.0.0.0', port=8000, log_level='info')" &
BACKEND_PID=$!

# 等待后端就绪（最多 30 秒）
BACKEND_READY=false
for i in $(seq 1 30); do
  if curl -s http://localhost:8000/api/v1/health >/dev/null 2>&1; then
    echo -e "${GREEN}✅ 后端已就绪${NC}"
    BACKEND_READY=true
    break
  fi
  # 检查后端进程是否已退出
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo -e "${RED}❌ 后端启动失败，请检查配置（.env 文件）${NC}"
    exit 1
  fi
  sleep 1
done

if [ "$BACKEND_READY" = false ]; then
  echo -e "${RED}❌ 后端启动超时${NC}"
  exit 1
fi

# 启动前端
echo -e "${CYAN}▶ 启动 Next.js 前端 (端口 3000)...${NC}"
(cd web && exec npm run dev -- -p 3000) &
FRONTEND_PID=$!

sleep 3
echo ""
echo -e "${GREEN}════════════════════════════════════════${NC}"
echo -e "${GREEN}  ExcelManus 已启动！${NC}"
echo -e "${GREEN}  前端: http://localhost:3000${NC}"
echo -e "${GREEN}  后端: http://localhost:8000${NC}"
echo -e "${GREEN}  按 Ctrl+C 停止所有服务${NC}"
echo -e "${GREEN}════════════════════════════════════════${NC}"
echo ""

wait
