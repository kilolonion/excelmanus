#!/usr/bin/env bash
set -euo pipefail

# ═══════════════════════════════════════════════════════════════════════
#  ExcelManus 通用部署脚本
#
#  支持多种部署拓扑：
#    • 单机部署（前后端同一台服务器）
#    • 前后端分离（两台服务器）
#    • Docker Compose 部署
#    • 本地开发部署
#
#  配置优先级：命令行参数 > 环境变量 > deploy/.env.deploy > 内置默认值
#
#  用法:  ./deploy.sh [选项]
#
#  基本选项:
#    --backend-only       只更新后端
#    --frontend-only      只更新前端
#    --full               完整部署（默认）
#    --skip-build         跳过前端构建（仅同步+重启）
#    --frontend-artifact FILE
#                         使用本地/CI 构建的前端制品（tar.gz），上传后原子切换
#    --skip-deps          跳过依赖安装
#    --cold-build         远端构建前清理 web/.next/cache（高风险，默认关闭）
#    --from-local         从本地 rsync 同步（默认从 GitHub 拉取）
#    --dry-run            仅打印将执行的操作，不实际执行
#
#  拓扑选项:
#    --single-server      单机部署模式（前后端同一台服务器）
#    --split-server       前后端分离模式（默认，需配置两台服务器）
#    --docker             Docker Compose 部署
#    --local              本地开发部署（不走 SSH）
#
#  服务器选项（覆盖配置文件）:
#    --backend-host HOST  后端服务器地址
#    --frontend-host HOST 前端服务器地址
#    --host HOST          单机模式的服务器地址
#    --user USER          SSH 用户名（默认 root）
#    --key PATH           SSH 私钥路径
#    --port PORT          SSH 端口（默认 22）
#
#  路径选项:
#    --backend-dir DIR    后端远程目录
#    --frontend-dir DIR   前端远程目录
#    --dir DIR            单机模式的项目目录
#
#  构建选项:
#    --node-bin PATH      Node.js bin 目录（远程服务器）
#    --python PATH        Python 可执行文件路径
#    --venv PATH          Python venv 目录（相对于后端目录）
#    --pm2-backend NAME   后端 PM2 进程名（默认 excelmanus-api）
#    --pm2-frontend NAME  前端 PM2 进程名（默认 excelmanus-web）
#    --backend-port PORT  后端 API 端口（默认 8000）
#    --frontend-port PORT 前端端口（默认 3000）
#    --keep-frontend-releases N
#                         前端制品部署后保留的回滚备份数量（默认 3）
#
#  Git 选项:
#    --repo URL           Git 仓库地址
#    --branch NAME        Git 分支（默认 main）
#
#  验证选项:
#    --health-url URL     健康检查 URL
#    --no-verify          跳过部署后验证
#    --verify-timeout SEC 健康检查超时（默认 30）
#
#  其他:
#    -v, --verbose        详细输出
#    -q, --quiet          静默模式（仅输出错误）
#    -h, --help           显示帮助
#    --version            显示版本
# ═══════════════════════════════════════════════════════════════════════

VERSION="2.0.0"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── 颜色 ──
if [[ -t 1 ]]; then
  RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'
  BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
else
  RED=''; GREEN=''; YELLOW=''; BLUE=''; CYAN=''; BOLD=''; NC=''
fi

# ── 日志函数 ──
VERBOSE=false
QUIET=false
DRY_RUN=false

log()     { [[ "$QUIET" == true ]] && return; echo -e "${GREEN}✅${NC} $*"; }
info()    { [[ "$QUIET" == true ]] && return; echo -e "${BLUE}ℹ️${NC}  $*"; }
warn()    { echo -e "${YELLOW}⚠️${NC}  $*" >&2; }
error()   { echo -e "${RED}❌${NC} $*" >&2; }
debug() {
  if [[ "$VERBOSE" == true ]]; then
    echo -e "${CYAN}🔍${NC} $*"
  fi
}
step()    { [[ "$QUIET" == true ]] && return; echo -e "\n${BOLD}$*${NC}"; }

run() {
  if [[ "$DRY_RUN" == true ]]; then
    echo -e "${YELLOW}[dry-run]${NC} $*"
    return 0
  fi
  debug "执行: $*"
  eval "$@"
}

# ── 默认值 ──
TOPOLOGY="auto"          # auto | single | split | docker | local
MODE="full"              # full | backend | frontend
SKIP_BUILD=false
SKIP_DEPS=false
FROM_LOCAL=false
NO_VERIFY=false
COLD_BUILD=false
FRONTEND_ARTIFACT=""
FRONTEND_RELEASE_KEEP=3
FRONTEND_ARTIFACT_REMOTE_PATH=""

# 服务器
BACKEND_HOST=""
FRONTEND_HOST=""
SSH_USER=""
SSH_KEY_PATH=""
SSH_PORT=""

# 路径
BACKEND_DIR=""
FRONTEND_DIR=""
NODE_BIN=""
PYTHON_BIN=""
VENV_DIR=""

# 进程
PM2_BACKEND=""
PM2_FRONTEND=""
BACKEND_PORT=""
FRONTEND_PORT=""

# Git
REPO_URL=""
REPO_BRANCH=""

# 验证
HEALTH_URL=""
VERIFY_TIMEOUT=""

# ── 加载配置文件 ──
_load_config() {
  local config_file="${SCRIPT_DIR}/deploy/.env.deploy"
  if [[ -f "$config_file" ]]; then
    debug "加载配置: $config_file"
    # shellcheck source=/dev/null
    source "$config_file"

    # 映射旧配置名到新变量（向后兼容）
    [[ -z "$BACKEND_HOST" && -n "${BACKEND_SERVER:-}" ]]     && BACKEND_HOST="$BACKEND_SERVER"
    [[ -z "$FRONTEND_HOST" && -n "${FRONTEND_SERVER:-}" ]]   && FRONTEND_HOST="$FRONTEND_SERVER"
    [[ -z "$SSH_USER" && -n "${SERVER_USER:-}" ]]            && SSH_USER="$SERVER_USER"
    [[ -z "$BACKEND_DIR" && -n "${BACKEND_REMOTE_DIR:-}" ]]  && BACKEND_DIR="$BACKEND_REMOTE_DIR"
    [[ -z "$FRONTEND_DIR" && -n "${FRONTEND_REMOTE_DIR:-}" ]] && FRONTEND_DIR="$FRONTEND_REMOTE_DIR"
    [[ -z "$NODE_BIN" && -n "${FRONTEND_NODE_BIN:-}" ]]      && NODE_BIN="$FRONTEND_NODE_BIN"
    [[ -z "$SSH_KEY_PATH" && -n "${SSH_KEY_NAME:-}" ]]       && SSH_KEY_PATH="${SCRIPT_DIR}/${SSH_KEY_NAME}"
    [[ -z "$REPO_URL" && -n "${REPO_URL:-}" ]]               || true
    [[ -z "$REPO_BRANCH" && -n "${REPO_BRANCH:-}" ]]         || true
  else
    debug "未找到配置文件: $config_file（使用默认值）"
  fi
}

# ── 应用默认值 ──
_apply_defaults() {
  SSH_USER="${SSH_USER:-root}"
  SSH_PORT="${SSH_PORT:-22}"
  BACKEND_DIR="${BACKEND_DIR:-/www/wwwroot/excelmanus}"
  FRONTEND_DIR="${FRONTEND_DIR:-${BACKEND_DIR}}"
  NODE_BIN="${NODE_BIN:-/usr/local/bin}"
  PYTHON_BIN="${PYTHON_BIN:-python3}"
  VENV_DIR="${VENV_DIR:-venv}"
  PM2_BACKEND="${PM2_BACKEND:-excelmanus-api}"
  PM2_FRONTEND="${PM2_FRONTEND:-excelmanus-web}"
  BACKEND_PORT="${BACKEND_PORT:-8000}"
  FRONTEND_PORT="${FRONTEND_PORT:-3000}"
  REPO_URL="${REPO_URL:-https://github.com/kilolonion/excelmanus}"
  REPO_BRANCH="${REPO_BRANCH:-main}"
  VERIFY_TIMEOUT="${VERIFY_TIMEOUT:-30}"

  # 自动检测拓扑
  if [[ "$TOPOLOGY" == "auto" ]]; then
    if [[ -n "$BACKEND_HOST" && -n "$FRONTEND_HOST" && "$BACKEND_HOST" != "$FRONTEND_HOST" ]]; then
      TOPOLOGY="split"
    elif [[ -n "$BACKEND_HOST" || -n "$FRONTEND_HOST" ]]; then
      TOPOLOGY="single"
      # 单机模式：统一使用同一个 host
      BACKEND_HOST="${BACKEND_HOST:-$FRONTEND_HOST}"
      FRONTEND_HOST="${FRONTEND_HOST:-$BACKEND_HOST}"
    else
      TOPOLOGY="local"
    fi
  fi

  # 单机模式下统一目录
  if [[ "$TOPOLOGY" == "single" ]]; then
    FRONTEND_DIR="${FRONTEND_DIR:-$BACKEND_DIR}"
    FRONTEND_HOST="${FRONTEND_HOST:-$BACKEND_HOST}"
  fi

  # 健康检查 URL
  if [[ -z "$HEALTH_URL" ]]; then
    if [[ -n "${SITE_URL:-}" ]]; then
      HEALTH_URL="${SITE_URL}/api/v1/health"
    elif [[ "$TOPOLOGY" == "local" ]]; then
      HEALTH_URL="http://localhost:${BACKEND_PORT}/api/v1/health"
    elif [[ -n "$BACKEND_HOST" ]]; then
      HEALTH_URL="http://${BACKEND_HOST}:${BACKEND_PORT}/api/v1/health"
    fi
  fi
}

# ── 解析参数 ──
_parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      # 基本选项
      --backend-only)    MODE="backend" ;;
      --frontend-only)   MODE="frontend" ;;
      --full)            MODE="full" ;;
      --skip-build)      SKIP_BUILD=true ;;
      --frontend-artifact) FRONTEND_ARTIFACT="$2"; shift ;;
      --skip-deps)       SKIP_DEPS=true ;;
      --cold-build)      COLD_BUILD=true ;;
      --from-local)      FROM_LOCAL=true ;;
      --dry-run)         DRY_RUN=true ;;

      # 拓扑
      --single-server)   TOPOLOGY="single" ;;
      --split-server)    TOPOLOGY="split" ;;
      --docker)          TOPOLOGY="docker" ;;
      --local)           TOPOLOGY="local" ;;

      # 服务器
      --backend-host)    BACKEND_HOST="$2"; shift ;;
      --frontend-host)   FRONTEND_HOST="$2"; shift ;;
      --host)            BACKEND_HOST="$2"; FRONTEND_HOST="$2"; TOPOLOGY="single"; shift ;;
      --user)            SSH_USER="$2"; shift ;;
      --key)             SSH_KEY_PATH="$2"; shift ;;
      --port)            SSH_PORT="$2"; shift ;;

      # 路径
      --backend-dir)     BACKEND_DIR="$2"; shift ;;
      --frontend-dir)    FRONTEND_DIR="$2"; shift ;;
      --dir)             BACKEND_DIR="$2"; FRONTEND_DIR="$2"; shift ;;

      # 构建
      --node-bin)        NODE_BIN="$2"; shift ;;
      --python)          PYTHON_BIN="$2"; shift ;;
      --venv)            VENV_DIR="$2"; shift ;;
      --pm2-backend)     PM2_BACKEND="$2"; shift ;;
      --pm2-frontend)    PM2_FRONTEND="$2"; shift ;;
      --backend-port)    BACKEND_PORT="$2"; shift ;;
      --frontend-port)   FRONTEND_PORT="$2"; shift ;;
      --keep-frontend-releases) FRONTEND_RELEASE_KEEP="$2"; shift ;;

      # Git
      --repo)            REPO_URL="$2"; shift ;;
      --branch)          REPO_BRANCH="$2"; shift ;;

      # 验证
      --health-url)      HEALTH_URL="$2"; shift ;;
      --no-verify)       NO_VERIFY=true ;;
      --verify-timeout)  VERIFY_TIMEOUT="$2"; shift ;;

      # 其他
      -v|--verbose)      VERBOSE=true ;;
      -q|--quiet)        QUIET=true ;;
      --version)         echo "ExcelManus Deploy v${VERSION}"; exit 0 ;;
      -h|--help)         _show_help; exit 0 ;;
      *)                 error "未知参数: $1"; echo "使用 --help 查看帮助"; exit 1 ;;
    esac
    shift
  done
}

_show_help() {
  # 提取脚本头部注释作为帮助
  sed -n '/^#  用法/,/^# ═/p' "${BASH_SOURCE[0]}" | sed 's/^# *//' | sed '$d'
  echo ""
  echo "示例:"
  echo "  # 单机部署（前后端同一台服务器）"
  echo "  ./deploy.sh --host 192.168.1.100 --dir /opt/excelmanus"
  echo ""
  echo "  # 前后端分离部署"
  echo "  ./deploy.sh --backend-host 10.0.0.1 --frontend-host 10.0.0.2"
  echo ""
  echo "  # 只更新后端，从本地同步"
  echo "  ./deploy.sh --backend-only --from-local"
  echo ""
  echo "  # Docker 部署"
  echo "  ./deploy.sh --docker"
  echo ""
  echo "  # 本地开发部署"
  echo "  ./deploy.sh --local --skip-deps"
  echo ""
  echo "  # 自定义 Node.js 路径和 PM2 进程名"
  echo "  ./deploy.sh --host myserver --node-bin /usr/local/node/bin --pm2-backend my-api"
  echo ""
  echo "  # 使用本地构建的前端制品（推荐低内存服务器）"
  echo "  ./deploy.sh --frontend-only --frontend-artifact ./web-dist/frontend-standalone.tar.gz"
  echo ""
  echo "  # 远端冷构建（仅排障使用）"
  echo "  ./deploy.sh --frontend-only --cold-build"
}

# ── SSH 执行封装 ──
_ssh_opts() {
  local opts="-o StrictHostKeyChecking=no -o ConnectTimeout=10 -o ServerAliveInterval=30 -o ServerAliveCountMax=6 -o TCPKeepAlive=yes"
  [[ -n "$SSH_KEY_PATH" ]] && opts="$opts -i $SSH_KEY_PATH"
  [[ "$SSH_PORT" != "22" ]] && opts="$opts -p $SSH_PORT"
  echo "$opts"
}

_remote() {
  local host="$1"; shift
  local cmd="$*"
  if [[ "$TOPOLOGY" == "local" ]]; then
    run "bash -c '$cmd'"
  else
    run "ssh $(_ssh_opts) ${SSH_USER}@${host} '$cmd'"
  fi
}

_remote_backend()  { _remote "$BACKEND_HOST" "$@"; }
_remote_frontend() { _remote "$FRONTEND_HOST" "$@"; }

_ensure_frontend_standalone_assets() {
  info "复制 standalone 静态资源..."
  _remote_frontend "
    cd '${FRONTEND_DIR}/web' && \
    if [[ -d .next/standalone ]]; then
      cp -r public .next/standalone/ 2>/dev/null || true
      cp -r .next/static .next/standalone/.next/ 2>/dev/null || true
      echo 'standalone 静态资源复制完成'
    else
      echo '未检测到 standalone 输出，跳过静态资源复制'
    fi
  "
}

_restart_frontend_service() {
  _remote_frontend "
    export PATH=${NODE_BIN}:\$PATH && \
    cd '${FRONTEND_DIR}/web' && \
    pm2 restart '${PM2_FRONTEND}' 2>/dev/null || \
    pm2 start .next/standalone/server.js --name '${PM2_FRONTEND}' --cwd '${FRONTEND_DIR}/web' 2>/dev/null
  "
}

_build_frontend_remote() {
  local cold_cmd=""
  if [[ "$COLD_BUILD" == true ]]; then
    warn "已启用 --cold-build：将清理远端 web/.next/cache 后再构建（低内存机器风险更高）"
    cold_cmd="rm -rf .next/cache && "
  else
    info "保留远端 .next/cache 以降低冷启动构建内存峰值。需要冷构建时请显式传 --cold-build。"
  fi

  info "构建前端（默认命令：npm run build）..."
  if _remote_frontend "
    export PATH=${NODE_BIN}:\$PATH && \
    cd '${FRONTEND_DIR}/web' && \
    ${cold_cmd}npm run build 2>&1 | tail -10
  "; then
    return 0
  fi

  warn "默认构建失败，尝试 webpack 兜底（npm run build:webpack）..."
  _remote_frontend "
    export PATH=${NODE_BIN}:\$PATH && \
    cd '${FRONTEND_DIR}/web' && \
    ${cold_cmd}npm run build:webpack 2>&1 | tail -10
  "
}

_upload_frontend_artifact() {
  local artifact_path="$1"
  local artifact_name
  artifact_name="$(basename "$artifact_path")"
  local remote_dir="${FRONTEND_DIR}/web/.deploy/artifacts"
  local remote_path="${remote_dir}/${artifact_name}"

  info "上传前端制品（支持断点续传）..."
  _remote_frontend "mkdir -p '${remote_dir}'"

  if [[ "$TOPOLOGY" == "local" ]]; then
    run "cp '${artifact_path}' '${remote_path}'"
  else
    local rsync_ssh="ssh $(_ssh_opts)"
    run "rsync -az --partial --append-verify --timeout=120 --progress -e \"$rsync_ssh\" \
      '${artifact_path}' '${SSH_USER}@${FRONTEND_HOST}:${remote_path}'"
  fi

  FRONTEND_ARTIFACT_REMOTE_PATH="$remote_path"
}

_activate_frontend_artifact() {
  local remote_artifact="$1"
  local release_id="$2"
  local prune_offset=$((FRONTEND_RELEASE_KEEP + 1))

  _remote_frontend "
    set -e
    WEB_DIR='${FRONTEND_DIR}/web'
    DEPLOY_DIR=\"\$WEB_DIR/.deploy\"
    STAGE_DIR=\"\$DEPLOY_DIR/stage-${release_id}\"
    BACKUP_DIR=\"\$DEPLOY_DIR/backups/${release_id}\"

    mkdir -p \"\$DEPLOY_DIR/backups\" \"\$STAGE_DIR\"
    tar -xzf '${remote_artifact}' -C \"\$STAGE_DIR\"

    [[ -f \"\$STAGE_DIR/.next/standalone/server.js\" ]] || { echo '制品缺少 .next/standalone/server.js'; exit 1; }
    [[ -d \"\$STAGE_DIR/.next/static\" ]] || { echo '制品缺少 .next/static'; exit 1; }
    [[ -d \"\$STAGE_DIR/public\" ]] || mkdir -p \"\$STAGE_DIR/public\"

    mkdir -p \"\$BACKUP_DIR/.next\"
    [[ -d \"\$WEB_DIR/.next/standalone\" ]] && mv \"\$WEB_DIR/.next/standalone\" \"\$BACKUP_DIR/.next/standalone\"
    [[ -d \"\$WEB_DIR/.next/static\" ]] && mv \"\$WEB_DIR/.next/static\" \"\$BACKUP_DIR/.next/static\"
    [[ -d \"\$WEB_DIR/public\" ]] && mv \"\$WEB_DIR/public\" \"\$BACKUP_DIR/public\"

    mkdir -p \"\$WEB_DIR/.next\"
    mv \"\$STAGE_DIR/.next/standalone\" \"\$WEB_DIR/.next/standalone\"
    mv \"\$STAGE_DIR/.next/static\" \"\$WEB_DIR/.next/static\"
    rm -rf \"\$WEB_DIR/public\"
    mv \"\$STAGE_DIR/public\" \"\$WEB_DIR/public\"

    rm -rf \"\$STAGE_DIR\" '${remote_artifact}'
    printf '%s' \"\$BACKUP_DIR\" > \"\$DEPLOY_DIR/last_backup_path\"

    if [[ -d \"\$DEPLOY_DIR/backups\" ]]; then
      old_backups=\$(ls -1dt \"\$DEPLOY_DIR\"/backups/* 2>/dev/null | tail -n +${prune_offset} || true)
      if [[ -n "\$old_backups" ]]; then
        while IFS= read -r one_backup; do
          [[ -n "\$one_backup" ]] && rm -rf "\$one_backup"
        done <<< "\$old_backups"
      fi
    fi
  "
}

_rollback_frontend_from_last_backup() {
  _remote_frontend "
    set -e
    WEB_DIR='${FRONTEND_DIR}/web'
    DEPLOY_DIR=\"\$WEB_DIR/.deploy\"
    [[ -f \"\$DEPLOY_DIR/last_backup_path\" ]] || { echo '未找到可回滚备份'; exit 1; }

    BACKUP_DIR=\$(cat "\$DEPLOY_DIR/last_backup_path")
    [[ -d "\$BACKUP_DIR" ]] || { echo '回滚失败：备份目录不存在'; exit 1; }

    if [[ ! -d \"\$BACKUP_DIR/.next/standalone\" || ! -d \"\$BACKUP_DIR/.next/static\" ]]; then
      echo '回滚失败：备份不完整，已保留当前版本'
      exit 1
    fi

    mkdir -p \"\$WEB_DIR/.next\"
    rm -rf \"\$WEB_DIR/.next/standalone\" \"\$WEB_DIR/.next/static\" \"\$WEB_DIR/public\"

    [[ -d \"\$BACKUP_DIR/.next/standalone\" ]] && mv \"\$BACKUP_DIR/.next/standalone\" \"\$WEB_DIR/.next/standalone\"
    [[ -d \"\$BACKUP_DIR/.next/static\" ]] && mv \"\$BACKUP_DIR/.next/static\" \"\$WEB_DIR/.next/static\"
    [[ -d \"\$BACKUP_DIR/public\" ]] && mv \"\$BACKUP_DIR/public\" \"\$WEB_DIR/public\"
  "
}

# ── rsync 排除列表 ──
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
  --exclude='users/'
  --exclude='*.pem'
  --exclude='.venv'
  --exclude='venv'
  --exclude='.worktrees'
  --exclude='.excelmanus'
  --exclude='.cursor'
  --exclude='.codex'
  --exclude='.agents'
  --exclude='.kiro'
  --exclude='build'
  --exclude='dist'
  --exclude='*.egg-info'
  --exclude='.pytest_cache'
  --exclude='.mypy_cache'
  --exclude='bench_results'
  --exclude='agent-transcripts'
  --exclude='.DS_Store'
  --exclude='outputs/'
)

# ── 代码同步 ──
_sync_code() {
  local host="$1" remote_dir="$2" label="$3"

  if [[ "$FROM_LOCAL" == true ]]; then
    info "从本地 rsync 同步代码到 ${label} (${host:-localhost})..."
    if [[ "$TOPOLOGY" == "local" ]]; then
      # 本地模式不需要 rsync
      debug "本地模式，跳过同步"
      return
    fi
    local rsync_ssh="ssh $(_ssh_opts)"
    run "rsync -az --partial --append-verify --timeout=120 ${_rsync_excludes[*]} --progress -e \"$rsync_ssh\" \
      '${SCRIPT_DIR}/' '${SSH_USER}@${host}:${remote_dir}/'"
  else
    info "从 GitHub 拉取更新到 ${label} (${host:-localhost})..."
    local git_cmd="
      set -e
      cd '${remote_dir}'
      if [[ ! -d .git ]]; then
        echo '仓库不存在，正在克隆...'
        cd /
        rm -rf '${remote_dir}'
        git clone '${REPO_URL}' '${remote_dir}'
        cd '${remote_dir}'
      else
        git fetch '${REPO_URL}' '${REPO_BRANCH}' && git reset --hard FETCH_HEAD
      fi
    "
    if [[ "$TOPOLOGY" == "local" ]]; then
      run "bash -c \"$git_cmd\""
    else
      _remote "$host" "$git_cmd"
    fi
  fi
  log "${label} 代码同步完成"
}

# ── 后端部署 ──
_deploy_backend() {
  step "🐍 部署后端..."

  # 同步代码
  _sync_code "$BACKEND_HOST" "$BACKEND_DIR" "后端"

  # 安装依赖
  if [[ "$SKIP_DEPS" != true ]]; then
    info "安装 Python 依赖..."
    _remote_backend "
      cd '${BACKEND_DIR}' && \
      source '${VENV_DIR}/bin/activate' && \
      pip install -e . -q && \
      pip install 'httpx[socks]' -q 2>/dev/null || true
    "
  fi

  # 重启后端
  info "重启后端服务..."
  _remote_backend "
    export PATH=${NODE_BIN}:\$PATH && \
    pm2 restart '${PM2_BACKEND}' --update-env 2>/dev/null || \
    pm2 start '${BACKEND_DIR}/${VENV_DIR}/bin/python -c \"import uvicorn; uvicorn.run(\\\"excelmanus.api:app\\\", host=\\\"0.0.0.0\\\", port=${BACKEND_PORT}, log_level=\\\"info\\\")\"' \
      --name '${PM2_BACKEND}' --cwd '${BACKEND_DIR}' 2>/dev/null || true
  "
  log "后端部署完成"
}

# ── 前端部署 ──
_deploy_frontend() {
  step "🌐 部署前端..."

  # 同步代码（分离模式下前端有独立的代码目录）
  if [[ "$TOPOLOGY" == "split" ]]; then
    if [[ -n "$FRONTEND_ARTIFACT" ]]; then
      info "已启用前端制品模式，跳过仓库同步。"
      _remote_frontend "mkdir -p '${FRONTEND_DIR}/web/.deploy/artifacts'"
    else
      _sync_code "$FRONTEND_HOST" "$FRONTEND_DIR" "前端"
    fi
  fi

  if [[ -n "$FRONTEND_ARTIFACT" ]]; then
    local release_id
    release_id="$(date +%Y%m%dT%H%M%S)"
    local remote_artifact
    _upload_frontend_artifact "$FRONTEND_ARTIFACT"
    remote_artifact="$FRONTEND_ARTIFACT_REMOTE_PATH"

    info "解包前端制品并切换到新版本..."
    if ! _activate_frontend_artifact "$remote_artifact" "$release_id"; then
      warn "前端制品激活失败，尝试回滚到上一版本..."
      _rollback_frontend_from_last_backup || warn "自动回滚失败，请手动检查 ${FRONTEND_DIR}/web/.deploy/backups"
      _restart_frontend_service || true
      error "前端制品部署失败（激活阶段）"
      return 1
    fi

    info "重启前端服务..."
    if ! _restart_frontend_service; then
      warn "前端重启失败，尝试回滚到上一版本..."
      _rollback_frontend_from_last_backup || warn "自动回滚失败，请手动检查 ${FRONTEND_DIR}/web/.deploy/backups"
      _restart_frontend_service || true
      error "前端制品部署失败（已执行回滚尝试）"
      return 1
    fi

    log "前端制品部署完成"
    return 0
  fi

  if [[ "$SKIP_BUILD" == true ]]; then
    info "跳过构建，仅重启..."
    _ensure_frontend_standalone_assets
    _restart_frontend_service
  else
    # 安装依赖
    if [[ "$SKIP_DEPS" != true ]]; then
      info "安装前端依赖..."
      _remote_frontend "
        export PATH=${NODE_BIN}:\$PATH && \
        cd '${FRONTEND_DIR}/web' && \
        npm install --production=false 2>&1 | tail -3
      "
    fi

    warn "当前为远端现场构建路径。低内存服务器建议使用 --frontend-artifact。"
    _build_frontend_remote
    _ensure_frontend_standalone_assets

    # 重启前端
    info "重启前端服务..."
    _restart_frontend_service
  fi
  log "前端部署完成"
}

# ── Docker 部署 ──
_deploy_docker() {
  step "🐳 Docker Compose 部署..."

  if [[ "$FROM_LOCAL" != true && "$TOPOLOGY" != "local" ]]; then
    _sync_code "${BACKEND_HOST:-localhost}" "$BACKEND_DIR" "Docker"
  fi

  local compose_cmd="docker compose"
  # 兼容旧版 docker-compose
  if ! command -v docker &>/dev/null || ! docker compose version &>/dev/null 2>&1; then
    compose_cmd="docker-compose"
  fi

  local docker_cmd="
    cd '${BACKEND_DIR}' && \
    ${compose_cmd} pull 2>/dev/null || true && \
    ${compose_cmd} up -d --build --remove-orphans
  "

  if [[ "$TOPOLOGY" == "local" || -z "$BACKEND_HOST" ]]; then
    run "bash -c \"$docker_cmd\""
  else
    _remote_backend "$docker_cmd"
  fi
  log "Docker 部署完成"
}

# ── 健康检查 ──
_verify() {
  if [[ "$NO_VERIFY" == true || -z "$HEALTH_URL" ]]; then
    return
  fi

  step "🔍 验证部署..."
  info "等待服务启动..."
  sleep 5

  local attempts=0
  local max_attempts=$(( VERIFY_TIMEOUT / 5 ))
  [[ $max_attempts -lt 1 ]] && max_attempts=1

  while [[ $attempts -lt $max_attempts ]]; do
    local status
    status=$(curl -s --max-time 10 "$HEALTH_URL" 2>/dev/null \
      | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null \
      || echo "")

    if [[ "$status" == "ok" ]]; then
      log "部署验证通过！服务正常运行"
      [[ -n "${SITE_URL:-}" ]] && info "访问地址: ${SITE_URL}"
      return
    fi

    attempts=$((attempts + 1))
    [[ $attempts -lt $max_attempts ]] && sleep 5
  done

  warn "健康检查未通过（${HEALTH_URL}）"
  warn "请检查日志:"
  [[ -n "$BACKEND_HOST" ]]  && warn "  后端: ssh ${SSH_USER}@${BACKEND_HOST} 'pm2 logs ${PM2_BACKEND} --lines 20 --nostream'"
  [[ -n "$FRONTEND_HOST" ]] && warn "  前端: ssh ${SSH_USER}@${FRONTEND_HOST} 'pm2 logs ${PM2_FRONTEND} --lines 20 --nostream'"
}

# ── 打印配置摘要 ──
_print_summary() {
  [[ "$QUIET" == true ]] && return

  echo ""
  echo -e "${BOLD}══════════════════════════════════════${NC}"
  echo -e "${BOLD}  ExcelManus Deploy v${VERSION}${NC}"
  echo -e "${BOLD}══════════════════════════════════════${NC}"
  echo ""
  echo -e "  拓扑:     ${CYAN}${TOPOLOGY}${NC}"
  echo -e "  模式:     ${CYAN}${MODE}${NC}"

  case "$TOPOLOGY" in
    split)
      echo -e "  后端:     ${CYAN}${SSH_USER}@${BACKEND_HOST}:${BACKEND_DIR}${NC}"
      echo -e "  前端:     ${CYAN}${SSH_USER}@${FRONTEND_HOST}:${FRONTEND_DIR}${NC}"
      ;;
    single)
      echo -e "  服务器:   ${CYAN}${SSH_USER}@${BACKEND_HOST}:${BACKEND_DIR}${NC}"
      ;;
    docker)
      echo -e "  目录:     ${CYAN}${BACKEND_DIR}${NC}"
      ;;
    local)
      echo -e "  目录:     ${CYAN}${BACKEND_DIR}${NC}"
      ;;
  esac

  echo -e "  代码来源: ${CYAN}$([ "$FROM_LOCAL" == true ] && echo "本地 rsync" || echo "GitHub (${REPO_BRANCH})")${NC}"
  [[ -n "$FRONTEND_ARTIFACT" ]] && echo -e "  前端制品: ${CYAN}${FRONTEND_ARTIFACT}${NC}"
  [[ "$COLD_BUILD" == true ]] && echo -e "  构建缓存: ${YELLOW}冷构建 (--cold-build)${NC}"
  [[ -n "$FRONTEND_ARTIFACT" ]] && echo -e "  回滚备份: ${CYAN}保留最近 ${FRONTEND_RELEASE_KEEP} 个${NC}"
  [[ "$SKIP_BUILD" == true ]] && echo -e "  构建:     ${YELLOW}跳过${NC}"
  [[ "$SKIP_DEPS" == true ]]  && echo -e "  依赖:     ${YELLOW}跳过${NC}"
  [[ "$DRY_RUN" == true ]]    && echo -e "  ${YELLOW}⚠️  DRY RUN 模式${NC}"
  echo ""
}

# ── 前置检查 ──
_preflight() {
  if [[ ! "$FRONTEND_RELEASE_KEEP" =~ ^[0-9]+$ ]] || [[ "$FRONTEND_RELEASE_KEEP" -lt 1 ]]; then
    error "--keep-frontend-releases 必须是 >= 1 的整数"
    exit 1
  fi

  if [[ -n "$FRONTEND_ARTIFACT" ]]; then
    if [[ ! -f "$FRONTEND_ARTIFACT" ]]; then
      error "前端制品不存在: $FRONTEND_ARTIFACT"
      exit 1
    fi
    if [[ "$MODE" == "backend" ]]; then
      warn "当前是 backend-only 模式，--frontend-artifact 不会生效"
    fi
  fi

  if [[ "$COLD_BUILD" == true && "$SKIP_BUILD" == true ]]; then
    warn "--cold-build 与 --skip-build 同时使用时，--cold-build 不生效"
  fi

  # SSH 密钥检查（非本地/Docker 模式）
  if [[ "$TOPOLOGY" != "local" && "$TOPOLOGY" != "docker" ]]; then
    if [[ -n "$SSH_KEY_PATH" && ! -f "$SSH_KEY_PATH" ]]; then
      error "SSH 私钥不存在: $SSH_KEY_PATH"
      exit 1
    fi
    [[ -n "$SSH_KEY_PATH" ]] && chmod 600 "$SSH_KEY_PATH" 2>/dev/null || true

    # 检查目标服务器可达性
    if [[ "$MODE" != "frontend" && -n "$BACKEND_HOST" ]]; then
      debug "检查后端服务器连通性..."
      if ! ssh $(_ssh_opts) -o BatchMode=yes "${SSH_USER}@${BACKEND_HOST}" "echo ok" &>/dev/null; then
        error "无法连接后端服务器: ${SSH_USER}@${BACKEND_HOST}"
        exit 1
      fi
    fi
    if [[ "$MODE" != "backend" && -n "$FRONTEND_HOST" && "$FRONTEND_HOST" != "$BACKEND_HOST" ]]; then
      debug "检查前端服务器连通性..."
      if ! ssh $(_ssh_opts) -o BatchMode=yes "${SSH_USER}@${FRONTEND_HOST}" "echo ok" &>/dev/null; then
        error "无法连接前端服务器: ${SSH_USER}@${FRONTEND_HOST}"
        exit 1
      fi
    fi
  fi
}

# ── 主流程 ──
main() {
  _load_config
  _parse_args "$@"
  _apply_defaults
  _print_summary
  _preflight

  case "$TOPOLOGY" in
    docker)
      _deploy_docker
      ;;
    *)
      [[ "$MODE" == "full" || "$MODE" == "backend" ]]  && _deploy_backend
      [[ "$MODE" == "full" || "$MODE" == "frontend" ]] && _deploy_frontend
      ;;
  esac

  _verify

  echo ""
  echo -e "${BOLD}══════════════════════════════════════${NC}"
  echo -e "${BOLD}  部署完成${NC}"
  echo -e "${BOLD}══════════════════════════════════════${NC}"
}

main "$@"
