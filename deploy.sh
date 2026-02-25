#!/usr/bin/env bash
set -euo pipefail

# ═══════════════════════════════════════════════════════════
#  ExcelManus 一键部署脚本（前后端分离）
#
#  所有服务器地址、端口、路径等配置从 deploy/.env.deploy 读取，
#  前端环境变量从 web/.env.production 读取。
#  本脚本不硬编码任何部署信息。
#
#  用法:  ./deploy.sh [选项]
#
#  选项:
#    --backend-only   只更新后端
#    --frontend-only  只更新前端
#    --full           完整部署（默认）
#    --skip-build     跳过前端构建（仅同步+重启）
#    --from-local     从本地 rsync 同步（默认从 GitHub 拉取）
# ═══════════════════════════════════════════════════════════

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── 加载部署配置 ──
DEPLOY_ENV="${SCRIPT_DIR}/deploy/.env.deploy"
if [[ ! -f "${DEPLOY_ENV}" ]]; then
  echo "❌ 未找到部署配置: ${DEPLOY_ENV}"
  echo "   请复制 deploy/.env.deploy.example 并填入真实值"
  exit 1
fi
# shellcheck source=deploy/.env.deploy
source "${DEPLOY_ENV}"

# ── 派生变量 ──
SSH_KEY="${SCRIPT_DIR}/${SSH_KEY_NAME}"

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
      echo ""
      echo "  配置文件: ${DEPLOY_ENV}"
      echo "  后端: ${BACKEND_SERVER}  前端: ${FRONTEND_SERVER}"
      echo "  默认从 GitHub 拉取，--from-local 则从本地 rsync 同步"
      exit 0 ;;
    *) echo "未知参数: $arg"; exit 1 ;;
  esac
done

SSH_OPTS="-i ${SSH_KEY} -o StrictHostKeyChecking=no -o ConnectTimeout=10 -o ServerAliveInterval=30"

_remote_backend() {
  ssh ${SSH_OPTS} ${SERVER_USER}@${BACKEND_SERVER} "$1"
}

_remote_frontend() {
  ssh ${SSH_OPTS} ${SERVER_USER}@${FRONTEND_SERVER} "$1"
}

_rsync_excludes=(
  --exclude='.git'
  --exclude='node_modules'
  --exclude='web/node_modules'
  --exclude='web/.next'
  --exclude='__pycache__'
  --exclude='*.pyc'
  --exclude='.env'
  --exclude='.env.local'
  --exclude='data/'
  --exclude='workspace/'
  --exclude='*.pem'
  --exclude='.venv'
  --exclude='venv'
  --exclude='.worktrees'
  --exclude='.excelmanus'
  --exclude='.cursor'
  --exclude='.codex'
  --exclude='.agents'
  --exclude='build'
  --exclude='dist'
  --exclude='*.egg-info'
  --exclude='.pytest_cache'
  --exclude='.mypy_cache'
  --exclude='bench_results'
  --exclude='agent-transcripts'
  --exclude='.DS_Store'
)

echo "══════════════════════════════════════"
echo "  ExcelManus 部署 (模式: ${MODE})"
echo "  后端: ${BACKEND_SERVER}"
echo "  前端: ${FRONTEND_SERVER}"
echo "  域名: ${SITE_URL:-未配置}"
echo "══════════════════════════════════════"
echo ""

# ── 检查 SSH 密钥 ──
if [[ ! -f "${SSH_KEY}" ]]; then
  echo "❌ 未找到私钥: ${SSH_KEY}"
  exit 1
fi
chmod 600 "${SSH_KEY}" 2>/dev/null || true

# ── 同步代码 ──
_sync_code() {
  local target_server="$1"
  local remote_dir="$2"
  local label="$3"

  if [[ "$FROM_LOCAL" == true ]]; then
    echo "📦 从本地 rsync 同步代码到 ${label} (${target_server})..."
    rsync -az "${_rsync_excludes[@]}" \
      --progress \
      -e "ssh ${SSH_OPTS}" \
      "${SCRIPT_DIR}/" "${SERVER_USER}@${target_server}:${remote_dir}/"
  else
    echo "📦 从 GitHub 拉取更新到 ${label} (${target_server})..."
    ssh ${SSH_OPTS} ${SERVER_USER}@${target_server} "
      set -e
      cd ${remote_dir}
      if [[ ! -d .git ]]; then
        echo '仓库不存在，正在克隆...'
        cd /
        rm -rf ${remote_dir}
        git clone ${REPO_URL} ${remote_dir}
        cd ${remote_dir}
      else
        git fetch ${REPO_URL} ${REPO_BRANCH} && git reset --hard FETCH_HEAD
      fi
    "
  fi
  echo "✅ ${label} 代码同步完成"
  echo ""
}

# ── 后端部署 ──
if [[ "$MODE" == "full" || "$MODE" == "backend" ]]; then
  _sync_code "${BACKEND_SERVER}" "${BACKEND_REMOTE_DIR}" "后端"

  echo "🐍 更新后端..."
  _remote_backend "
    cd ${BACKEND_REMOTE_DIR} && \
    source venv/bin/activate && \
    pip install -e . -q && \
    pip install 'httpx[socks]' -q && \
    export PATH=${BACKEND_NODE_BIN}:\$PATH && \
    pm2 restart excelmanus-api --update-env
  "
  echo "✅ 后端更新完成"
  echo ""
fi

# ── 前端部署 ──
if [[ "$MODE" == "full" || "$MODE" == "frontend" ]]; then
  _sync_code "${FRONTEND_SERVER}" "${FRONTEND_REMOTE_DIR}" "前端"

  if [[ "$SKIP_BUILD" == true ]]; then
    echo "⏭️  跳过前端构建，仅重启..."
    # 确保 standalone 静态资源存在（可能被之前的操作清除）
    _remote_frontend "
      export PATH=${FRONTEND_NODE_BIN}:\$PATH && \
      cd ${FRONTEND_REMOTE_DIR}/web && \
      if [[ -d .next/standalone ]]; then
        cp -r public .next/standalone/ 2>/dev/null || true
        cp -r .next/static .next/standalone/.next/ 2>/dev/null || true
      fi && \
      pm2 restart excelmanus-web
    "
  else
    echo "🌐 更新前端（安装依赖 + 构建 + 静态资源 + 重启）..."
    # 前端构建时的环境变量从 web/.env.production 自动加载（Next.js 内置行为）。
    # Next.js standalone 模式构建后需要手动复制 public/ 和 .next/static/，
    # 否则 logo、图片、CSS 等静态资源会 404。
    # 启动方式必须用 node .next/standalone/server.js，不能用 npm start。
    _remote_frontend "
      export PATH=${FRONTEND_NODE_BIN}:\$PATH && \
      cd ${FRONTEND_REMOTE_DIR}/web && \
      npm install --production=false 2>&1 | tail -3 && \
      npm run build 2>&1 | tail -5 && \
      echo '📋 复制 standalone 静态资源...' && \
      cp -r public .next/standalone/ && \
      cp -r .next/static .next/standalone/.next/ && \
      echo '✅ 静态资源复制完成' && \
      pm2 restart excelmanus-web
    "
  fi
  echo "✅ 前端更新完成"
  echo ""
fi

# ── 验证 ──
echo "🔍 验证服务（等待启动）..."
sleep 8

HEALTH_URL="${SITE_URL:-https://kilon.top}/api/v1/health"
HEALTH=$(curl -s --max-time 10 "${HEALTH_URL}" 2>/dev/null || echo '{"status":"failed"}')
STATUS=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))" 2>/dev/null || echo "unknown")

if [[ "$STATUS" == "ok" ]]; then
  echo "✅ 部署成功！服务正常运行"
  echo ""
  echo "   🌐 ${SITE_URL:-https://kilon.top}"
  echo ""
else
  echo "⚠️  健康检查未通过，请检查日志:"
  echo "   后端: ssh -i ${SSH_KEY} ${SERVER_USER}@${BACKEND_SERVER} 'pm2 logs excelmanus-api --lines 20 --nostream'"
  echo "   前端: ssh -i ${SSH_KEY} ${SERVER_USER}@${FRONTEND_SERVER} 'pm2 logs excelmanus-web --lines 20 --nostream'"
fi

echo "══════════════════════════════════════"
echo "  部署完成"
echo "══════════════════════════════════════"
