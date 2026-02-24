#!/usr/bin/env bash
set -euo pipefail

# ═══════════════════════════════════════════════════════════
#  ExcelManus 一键部署脚本
#  用法:  ./deploy.sh [选项]
#
#  选项:
#    --backend-only   只更新后端
#    --frontend-only  只更新前端
#    --full           完整部署（默认）
#    --skip-build     跳过前端构建（仅同步+重启）
#    --from-local     从本地 rsync 同步（默认从 GitHub 拉取）
# ═══════════════════════════════════════════════════════════

# ── 配置 ──
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SSH_KEY="${SCRIPT_DIR}/id_excelmanus.pem"
SERVER="8.138.89.144"
SERVER_USER="root"
REMOTE_DIR="/www/wwwroot/excelmanus"
NODE_BIN="/www/server/nodejs/v22.22.0/bin"
REPO_URL="https://github.com/kilolonion/excelmanus"
REPO_BRANCH="main"

MODE="full"
SKIP_BUILD=false
FROM_LOCAL=false

for arg in "$@"; do
  case $arg in
    --backend-only)  MODE="backend" ;;
    --frontend-only) MODE="frontend" ;;
    --full)          MODE="full" ;;
    --skip-build)    SKIP_BUILD=true ;;
    --from-local)    FROM_LOCAL=true ;;
    -h|--help)
      echo "用法: ./deploy.sh [--backend-only|--frontend-only|--full] [--skip-build] [--from-local]"
      echo "  默认从 GitHub (${REPO_URL}) 拉取更新，--from-local 则从本地 rsync 同步"
      exit 0 ;;
    *) echo "未知参数: $arg"; exit 1 ;;
  esac
done

SSH_OPTS="-i ${SSH_KEY} -o StrictHostKeyChecking=no -o ConnectTimeout=10"

_remote() {
  ssh ${SSH_OPTS} ${SERVER_USER}@${SERVER} "$1"
}

echo "══════════════════════════════════════"
echo "  ExcelManus 部署 (模式: ${MODE})"
echo "══════════════════════════════════════"
echo ""

# ── 检查 SSH 密钥 ──
if [[ ! -f "${SSH_KEY}" ]]; then
  echo "❌ 未找到私钥: ${SSH_KEY}"
  echo "   请将 id_excelmanus.pem 放在脚本同目录下"
  exit 1
fi
chmod 600 "${SSH_KEY}" 2>/dev/null || true

# ── 同步代码 ──
if [[ "$FROM_LOCAL" == true ]]; then
  echo "📦 从本地 rsync 同步代码到服务器..."
  rsync -az \
    --exclude='.git' \
    --exclude='node_modules' \
    --exclude='web/node_modules' \
    --exclude='web/.next' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.env' \
    --exclude='data/' \
    --exclude='workspace/' \
    --exclude='*.pem' \
    --exclude='.venv' \
    --exclude='venv' \
    --exclude='.worktrees' \
    --exclude='.excelmanus' \
    --exclude='.cursor' \
    --exclude='.codex' \
    --exclude='.agents' \
    --exclude='build' \
    --exclude='dist' \
    --exclude='*.egg-info' \
    --exclude='.pytest_cache' \
    --exclude='.mypy_cache' \
    --exclude='bench_results' \
    --exclude='agent-transcripts' \
    --exclude='.DS_Store' \
    --progress \
    -e "ssh ${SSH_OPTS}" \
    "${SCRIPT_DIR}/" "${SERVER_USER}@${SERVER}:${REMOTE_DIR}/"
else
  echo "📦 从 GitHub 拉取更新 (${REPO_URL})..."
  _remote "
    set -e
    cd ${REMOTE_DIR}
    if [[ ! -d .git ]]; then
      echo '❌ 远程目录不是 git 仓库，请先克隆: git clone ${REPO_URL} .'
      exit 1
    fi
    git fetch ${REPO_URL} ${REPO_BRANCH} && git reset --hard FETCH_HEAD
  "
fi
echo "✅ 代码同步完成"
echo ""

# ── 后端部署 ──
if [[ "$MODE" == "full" || "$MODE" == "backend" ]]; then
  echo "🐍 更新后端..."
  _remote "
    cd ${REMOTE_DIR} && \
    source venv/bin/activate && \
    pip install -e . -q && \
    pip install 'httpx[socks]' -q && \
    export PATH=${NODE_BIN}:\$PATH && \
    pm2 restart excelmanus-api --update-env
  "
  echo "✅ 后端更新完成"
  echo ""
fi

# ── 前端部署 ──
if [[ "$MODE" == "full" || "$MODE" == "frontend" ]]; then
  if [[ "$SKIP_BUILD" == true ]]; then
    echo "⏭️  跳过前端构建，仅重启..."
    _remote "export PATH=${NODE_BIN}:\$PATH && pm2 restart excelmanus-web"
  else
    echo "🌐 更新前端（安装依赖 + 构建 + 重启）..."
    _remote "
      export PATH=${NODE_BIN}:\$PATH && \
      cd ${REMOTE_DIR}/web && \
      npm install --production=false 2>&1 | tail -3 && \
      NEXT_PUBLIC_BACKEND_ORIGIN= BACKEND_INTERNAL_URL=http://127.0.0.1:8000 npm run build 2>&1 | tail -5 && \
      pm2 restart excelmanus-web
    "
  fi
  echo "✅ 前端更新完成"
  echo ""
fi

# ── 验证 ──
echo "🔍 验证服务（等待启动）..."
sleep 8

HEALTH=$(curl -s --max-time 10 "https://kilon.top/api/v1/health" 2>/dev/null || echo '{"status":"failed"}')
STATUS=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))" 2>/dev/null || echo "unknown")

if [[ "$STATUS" == "ok" ]]; then
  echo "✅ 部署成功！服务正常运行"
  echo ""
  echo "   🌐 https://kilon.top"
  echo ""
else
  echo "⚠️  健康检查未通过，请检查日志:"
  echo "   ssh -i ${SSH_KEY} ${SERVER_USER}@${SERVER} 'pm2 logs --lines 20 --nostream'"
fi

echo "══════════════════════════════════════"
echo "  部署完成"
echo "══════════════════════════════════════"
