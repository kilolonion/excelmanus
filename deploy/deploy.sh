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
#  用法:  ./deploy/deploy.sh [命令] [选项]
#
#  命令:
#    deploy               执行部署（默认，可省略）
#    rollback             回滚到上一次部署
#    status               查看当前部署状态
#    check                检查部署环境依赖（含前后端互联检测）
#    init-env             首次部署：推送 .env 模板到远程服务器
#    history              查看部署历史
#    logs                 查看部署日志
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
#    --no-lock            跳过部署锁（允许并行部署，危险）
#    --force              强制执行（跳过确认提示）
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
#    --key PATH           SSH 私钥路径（全局，未指定独立密钥时回退使用）
#    --backend-key PATH   后端服务器 SSH 私钥路径（覆盖 --key）
#    --frontend-key PATH  前端服务器 SSH 私钥路径（覆盖 --key）
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
#    --service-manager MGR
#                         服务管理器: pm2（默认）| systemd
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
#  Hook 选项:
#    --pre-deploy SCRIPT  部署前执行的脚本
#    --post-deploy SCRIPT 部署后执行的脚本
#
#  其他:
#    -v, --verbose        详细输出
#    -q, --quiet          静默模式（仅输出错误）
#    -h, --help           显示帮助
#    --version            显示版本
# ═══════════════════════════════════════════════════════════════════════

VERSION="2.1.0"
# 远程服务器系统 PATH（解决 SSH 非交互会话 PATH 为空导致 sh/tail/git 找不到的问题）
REMOTE_SYSTEM_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ── 颜色 ──
if [[ -t 1 ]]; then
  RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'
  BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
else
  RED=''; GREEN=''; YELLOW=''; BLUE=''; CYAN=''; BOLD=''; NC=''
fi

# ── 操作系统检测 ──
_detect_os() {
  case "$(uname -s)" in
    Darwin*)  OS_TYPE="macos" ;;
    Linux*)   OS_TYPE="linux" ;;
    MINGW*|MSYS*|CYGWIN*) OS_TYPE="windows" ;;
    *)        OS_TYPE="unknown" ;;
  esac

  # Linux 发行版检测
  DISTRO_NAME=""
  PKG_MANAGER=""
  if [[ "$OS_TYPE" == "linux" ]]; then
    if [[ -f /etc/os-release ]]; then
      # shellcheck source=/dev/null
      DISTRO_NAME=$(. /etc/os-release && echo "${ID:-unknown}")
    fi
    if command -v apt-get &>/dev/null; then
      PKG_MANAGER="apt"
    elif command -v dnf &>/dev/null; then
      PKG_MANAGER="dnf"
    elif command -v yum &>/dev/null; then
      PKG_MANAGER="yum"
    elif command -v pacman &>/dev/null; then
      PKG_MANAGER="pacman"
    elif command -v zypper &>/dev/null; then
      PKG_MANAGER="zypper"
    elif command -v apk &>/dev/null; then
      PKG_MANAGER="apk"
    fi
  fi
}
_detect_os

# 根据 OS/包管理器生成安装提示
_install_hint() {
  local pkg="$1"
  case "$OS_TYPE" in
    macos) echo "brew install $pkg" ;;
    linux)
      case "$PKG_MANAGER" in
        apt)    echo "sudo apt install $pkg" ;;
        dnf)    echo "sudo dnf install $pkg" ;;
        yum)    echo "sudo yum install $pkg" ;;
        pacman) echo "sudo pacman -S $pkg" ;;
        zypper) echo "sudo zypper install $pkg" ;;
        apk)    echo "apk add $pkg" ;;
        *)      echo "请通过系统包管理器安装 $pkg" ;;
      esac
      ;;
    *) echo "请安装 $pkg" ;;
  esac
}

# ── 日志函数 ──
VERBOSE=false
QUIET=false
DRY_RUN=false
DEPLOY_LOG_FILE=""

_init_log_file() {
  local log_dir="${SCRIPT_DIR}/.deploy_logs"
  mkdir -p "$log_dir"
  DEPLOY_LOG_FILE="${log_dir}/deploy_$(date +%Y%m%dT%H%M%S).log"
  echo "# ExcelManus Deploy v${VERSION} — $(date '+%Y-%m-%d %H:%M:%S')" > "$DEPLOY_LOG_FILE"
  # 保留最近 20 个日志
  local old_logs
  old_logs=$(ls -1t "${log_dir}"/deploy_*.log 2>/dev/null | tail -n +21 || true)
  if [[ -n "$old_logs" ]]; then
    while IFS= read -r f; do [[ -n "$f" ]] && rm -f "$f" || true; done <<< "$old_logs"
  fi
}

_log_to_file() {
  [[ -n "$DEPLOY_LOG_FILE" ]] && echo "[$(date '+%H:%M:%S')] $*" >> "$DEPLOY_LOG_FILE" || true
}

log()     { _log_to_file "OK  $*"; [[ "$QUIET" == true ]] && return || true; echo -e "${GREEN}✅${NC} $*"; }
info()    { _log_to_file "INF $*"; [[ "$QUIET" == true ]] && return || true; echo -e "${BLUE}ℹ️${NC}  $*"; }
warn()    { _log_to_file "WRN $*"; echo -e "${YELLOW}⚠️${NC}  $*" >&2; }
error()   { _log_to_file "ERR $*"; echo -e "${RED}❌${NC} $*" >&2; }
debug() {
  _log_to_file "DBG $*"
  if [[ "$VERBOSE" == true ]]; then
    echo -e "${CYAN}🔍${NC} $*"
  fi
}
step()    { _log_to_file "=== $*"; [[ "$QUIET" == true ]] && return || true; echo -e "\n${BOLD}$*${NC}"; }

run() {
  if [[ "$DRY_RUN" == true ]]; then
    echo -e "${YELLOW}[dry-run]${NC} $*"
    _log_to_file "DRY $*"
    return 0
  fi
  debug "执行: $*"
  eval "$@"
}

# ── 默认值 ──
COMMAND="deploy"         # deploy | rollback | status | check | init-env | history | logs
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
NO_LOCK=false
FORCE=false

# 服务器
BACKEND_HOST=""
FRONTEND_HOST=""
SSH_USER=""
SSH_KEY_PATH=""
BACKEND_SSH_KEY_PATH=""
FRONTEND_SSH_KEY_PATH=""
SSH_PORT=""

# 路径
BACKEND_DIR=""
FRONTEND_DIR=""
NODE_BIN=""
PYTHON_BIN=""
VENV_DIR=""

# 进程
SERVICE_MANAGER=""       # pm2 | systemd
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

# Hooks
PRE_DEPLOY_HOOK=""
POST_DEPLOY_HOOK=""

# 多站点支持（由 _apply_defaults 解析）
_SITE_URLS=()  # 数组，从 SITE_URL / SITE_URLS 解析

# 内部状态
LOCK_FILE=""
DEPLOY_START_TIME=""

# ── 加载配置文件 ──
_load_config() {
  local config_file="${SCRIPT_DIR}/.env.deploy"
  if [[ -f "$config_file" ]]; then
    debug "加载配置: $config_file"
    # shellcheck source=/dev/null
    source "$config_file"

    # 映射旧配置名到新变量（向后兼容）
    [[ -z "$BACKEND_HOST" && -n "${BACKEND_SERVER:-}" ]]     && BACKEND_HOST="$BACKEND_SERVER" || true
    [[ -z "$FRONTEND_HOST" && -n "${FRONTEND_SERVER:-}" ]]   && FRONTEND_HOST="$FRONTEND_SERVER" || true
    [[ -z "$SSH_USER" && -n "${SERVER_USER:-}" ]]            && SSH_USER="$SERVER_USER" || true
    [[ -z "$BACKEND_DIR" && -n "${BACKEND_REMOTE_DIR:-}" ]]  && BACKEND_DIR="$BACKEND_REMOTE_DIR" || true
    [[ -z "$FRONTEND_DIR" && -n "${FRONTEND_REMOTE_DIR:-}" ]] && FRONTEND_DIR="$FRONTEND_REMOTE_DIR" || true
    [[ -z "$NODE_BIN" && -n "${FRONTEND_NODE_BIN:-}" ]]      && NODE_BIN="$FRONTEND_NODE_BIN" || true
    [[ -z "$SSH_KEY_PATH" && -n "${SSH_KEY_NAME:-}" ]]       && SSH_KEY_PATH="${PROJECT_ROOT}/${SSH_KEY_NAME}" || true
    [[ -z "$BACKEND_SSH_KEY_PATH" && -n "${BACKEND_SSH_KEY_NAME:-}" ]] && BACKEND_SSH_KEY_PATH="${PROJECT_ROOT}/${BACKEND_SSH_KEY_NAME}" || true
    [[ -z "$FRONTEND_SSH_KEY_PATH" && -n "${FRONTEND_SSH_KEY_NAME:-}" ]] && FRONTEND_SSH_KEY_PATH="${PROJECT_ROOT}/${FRONTEND_SSH_KEY_NAME}" || true
    # REPO_URL / REPO_BRANCH are set directly by `source` above — no extra mapping needed
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
  SERVICE_MANAGER="${SERVICE_MANAGER:-pm2}"
  EXCELMANUS_CHANNELS="${EXCELMANUS_CHANNELS:-qq}"
  export EXCELMANUS_CHANNELS

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

  # 每服务器独立密钥（未设置时回退到全局 SSH_KEY_PATH）
  BACKEND_SSH_KEY_PATH="${BACKEND_SSH_KEY_PATH:-$SSH_KEY_PATH}"
  FRONTEND_SSH_KEY_PATH="${FRONTEND_SSH_KEY_PATH:-$SSH_KEY_PATH}"

  # 解析多站点 URL：支持 SITE_URLS（多值）和 SITE_URL（单值，向后兼容）
  _SITE_URLS=()
  local _raw_urls="${SITE_URLS:-${SITE_URL:-}}"
  if [[ -n "$_raw_urls" ]]; then
    IFS=',' read -ra _SITE_URLS <<< "$_raw_urls"
    # 去除空白
    local _cleaned=()
    for _u in "${_SITE_URLS[@]}"; do
      _u="$(echo "$_u" | tr -d '[:space:]')"
      [[ -n "$_u" ]] && _cleaned+=("$_u")
    done
    _SITE_URLS=("${_cleaned[@]}")
    # 向后兼容：确保 SITE_URL 设为第一个值
    if [[ ${#_SITE_URLS[@]} -gt 0 ]]; then
      SITE_URL="${_SITE_URLS[0]}"
    fi
  fi

  # 健康检查 URL（使用第一个站点 URL）
  if [[ -z "$HEALTH_URL" ]]; then
    if [[ ${#_SITE_URLS[@]} -gt 0 ]]; then
      HEALTH_URL="${_SITE_URLS[0]}/api/v1/health"
    elif [[ "$TOPOLOGY" == "local" ]]; then
      HEALTH_URL="http://localhost:${BACKEND_PORT}/api/v1/health"
    elif [[ -n "$BACKEND_HOST" ]]; then
      HEALTH_URL="http://${BACKEND_HOST}:${BACKEND_PORT}/api/v1/health"
    fi
  fi
}

# ── 解析参数 ──
_parse_args() {
  # 第一个非 -- 开头参数视为命令
  if [[ $# -gt 0 && ! "$1" =~ ^- ]]; then
    case "$1" in
      deploy|rollback|rollback-to|status|check|init-env|history|logs) COMMAND="$1"; shift ;;
    esac
  fi

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
      --no-lock)         NO_LOCK=true ;;
      --force)           FORCE=true ;;

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
      --backend-key)     BACKEND_SSH_KEY_PATH="$2"; shift ;;
      --frontend-key)    FRONTEND_SSH_KEY_PATH="$2"; shift ;;
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
      --service-manager) SERVICE_MANAGER="$2"; shift ;;
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

      # 回滚目标
      --release)         ROLLBACK_RELEASE="$2"; shift ;;
      --commit)          ROLLBACK_COMMIT="$2"; shift ;;

      # Hooks
      --pre-deploy)      PRE_DEPLOY_HOOK="$2"; shift ;;
      --post-deploy)     POST_DEPLOY_HOOK="$2"; shift ;;

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
  echo "  ./deploy/deploy.sh --host 192.168.1.100 --dir /opt/excelmanus"
  echo ""
  echo "  # 前后端分离部署"
  echo "  ./deploy/deploy.sh --backend-host 10.0.0.1 --frontend-host 10.0.0.2"
  echo ""
  echo "  # 只更新后端，从本地同步"
  echo "  ./deploy/deploy.sh --backend-only --from-local"
  echo ""
  echo "  # Docker 部署"
  echo "  ./deploy/deploy.sh --docker"
  echo ""
  echo "  # 本地开发部署"
  echo "  ./deploy/deploy.sh --local --skip-deps"
  echo ""
  echo "  # 自定义 Node.js 路径和 PM2 进程名"
  echo "  ./deploy/deploy.sh --host myserver --node-bin /usr/local/node/bin --pm2-backend my-api"
  echo ""
  echo "  # 使用本地构建的前端制品（推荐低内存服务器）"
  echo "  ./deploy/deploy.sh --frontend-only --frontend-artifact ./web-dist/frontend-standalone.tar.gz"
  echo ""
  echo "  # 远端冷构建（仅排障使用）"
  echo "  ./deploy/deploy.sh --frontend-only --cold-build"
  echo ""
  echo "  # 回滚到上一版本"
  echo "  ./deploy/deploy.sh rollback"
  echo ""
  echo "  # 查看部署状态"
  echo "  ./deploy/deploy.sh status"
  echo ""
  echo "  # 检查环境依赖"
  echo "  ./deploy/deploy.sh check"
  echo ""
  echo "  # 查看部署历史"
  echo "  ./deploy/deploy.sh history"
  echo ""
  echo "  # 使用 systemd 管理服务"
  echo "  ./deploy/deploy.sh --service-manager systemd --host myserver"
  echo ""
  echo "  # 带 pre/post hook"
  echo "  ./deploy/deploy.sh --pre-deploy ./scripts/pre.sh --post-deploy ./scripts/post.sh"
  echo ""
  echo "  # 前后端分离 + 双密钥"
  echo "  ./deploy/deploy.sh --backend-host 10.0.0.1 --frontend-host 10.0.0.2 \\"
  echo "      --backend-key ~/.ssh/backend.pem --frontend-key ~/.ssh/frontend.pem"
  echo ""
  echo "  # 首次部署：推送 .env 模板到远程服务器"
  echo "  ./deploy/deploy.sh init-env --host 192.168.1.100"
}

# ── SSH 执行封装 ──
_ssh_opts() {
  local key_override="${1:-$SSH_KEY_PATH}"
  local opts="-o StrictHostKeyChecking=no -o ConnectTimeout=10 -o ServerAliveInterval=30 -o ServerAliveCountMax=6 -o TCPKeepAlive=yes"
  [[ -n "$key_override" ]] && opts="$opts -i $key_override" || true
  [[ "$SSH_PORT" != "22" ]] && opts="$opts -p $SSH_PORT" || true
  echo "$opts"
}

_remote() {
  local host="$1" key="$2"; shift 2
  local cmd="$*"
  # 自动注入系统 PATH（解决 SSH 非交互会话 PATH 为空的问题）
  local full_cmd="export PATH=${NODE_BIN}:${REMOTE_SYSTEM_PATH}:\$PATH && ${cmd}"
  if [[ "$TOPOLOGY" == "local" ]]; then
    run "bash -c '$full_cmd'"
  else
    run "ssh $(_ssh_opts "$key") ${SSH_USER}@${host} '$full_cmd'"
  fi
}

_remote_backend()  { _remote "$BACKEND_HOST" "$BACKEND_SSH_KEY_PATH" "$@"; }
_remote_frontend() { _remote "$FRONTEND_HOST" "$FRONTEND_SSH_KEY_PATH" "$@"; }

_ensure_frontend_standalone_assets() {
  info "检查 standalone 静态资源..."
  _remote_frontend "
    cd '${FRONTEND_DIR}/web'
    if [[ ! -d .next/standalone ]]; then
      echo '[WARN] 未检测到 standalone 目录。请确认 next.config.ts 包含 output: standalone 且构建使用了 --webpack'
      echo '[INFO] 将回退到 next start 启动'
      exit 0
    fi

    echo '[INFO] 检测到 standalone 目录，复制静态资源...'

    # 检测 server.js 的实际位置（可能在根级别或嵌套子目录中）
    # Next.js standalone 会镜像项目目录结构，monorepo 中 server.js 可能在子目录
    _server_js=\$(find .next/standalone -name 'server.js' -maxdepth 5 | head -1)
    if [[ -z \"\$_server_js\" ]]; then
      echo '[WARN] standalone/server.js 不存在'
      echo '[WARN] standalone 不完整，将回退到 next start'
      exit 0
    fi
    _server_dir=\$(dirname \"\$_server_js\")
    echo \"[INFO] server.js 位于: \$_server_js\"

    # 根级别（.next/standalone/）始终需要静态资源
    mkdir -p .next/standalone/.next
    rm -rf .next/standalone/.next/static
    rm -rf .next/standalone/public
    if [[ -d .next/static ]]; then
      cp -r .next/static .next/standalone/.next/static
    fi
    if [[ -d public ]]; then
      cp -r public .next/standalone/public
    fi

    # 如果 server.js 在嵌套子目录中，还需要在该子目录中也放一份静态资源
    if [[ \"\$_server_dir\" != '.next/standalone' ]]; then
      echo \"[INFO] server.js 在嵌套目录中（\$_server_dir），同步静态资源到该目录...\"
      mkdir -p \"\$_server_dir/.next\"
      rm -rf \"\$_server_dir/.next/static\"
      rm -rf \"\$_server_dir/public\"
      if [[ -d .next/static ]]; then
        cp -r .next/static \"\$_server_dir/.next/static\"
      fi
      if [[ -d public ]]; then
        cp -r public \"\$_server_dir/public\"
      fi
    fi

    # 验证关键文件存在（优先检查 server.js 所在目录）
    _ok=true
    if [[ ! -d \"\$_server_dir/.next/static/chunks\" ]]; then
      echo '[WARN] server.js 目录下 .next/static/chunks 不存在'
      _ok=false
    fi

    if [[ \"\$_ok\" == true ]]; then
      _chunk_count=\$(find \"\$_server_dir/.next/static/chunks\" -name '*.js' | wc -l)
      echo \"standalone 静态资源复制完成（\${_chunk_count} 个 JS chunks）\"
    else
      echo '[WARN] standalone 不完整，将回退到 next start'
    fi
  "
}

_restart_frontend_service() {
  if [[ "$SERVICE_MANAGER" == "systemd" ]]; then
    # systemd: 自动检测 standalone vs next start（支持嵌套目录）
    _remote_frontend "
      WEB_DIR='${FRONTEND_DIR}/web'
      NODE_CMD=\$(command -v node 2>/dev/null || echo '${NODE_BIN}/node')
      _server_js=\$(find \"\$WEB_DIR/.next/standalone\" -name 'server.js' -maxdepth 5 2>/dev/null | head -1)
      if [[ -n \"\$_server_js\" ]]; then
        EXEC_START=\"\$NODE_CMD \$_server_js\"
        echo \"[INFO] 使用 standalone 模式启动: \$_server_js\"
      else
        NPX_CMD=\$(command -v npx 2>/dev/null || echo '${NODE_BIN}/npx')
        EXEC_START=\"\$NPX_CMD next start -p ${FRONTEND_PORT}\"
        echo \"[INFO] standalone 不存在，使用 next start 启动\"
      fi
      sudo tee /etc/systemd/system/${PM2_FRONTEND}.service > /dev/null <<SVCEOF
[Unit]
Description=ExcelManus Frontend
After=network.target

[Service]
Type=simple
WorkingDirectory=\$WEB_DIR
ExecStart=\$EXEC_START
Restart=on-failure
RestartSec=5
Environment=PORT=${FRONTEND_PORT}
Environment=PATH=${NODE_BIN}:/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=multi-user.target
SVCEOF
      sudo systemctl daemon-reload
      sudo systemctl enable '${PM2_FRONTEND}' 2>/dev/null || true
      sudo systemctl restart '${PM2_FRONTEND}'
    "
  else
    # PM2 or direct process: 自动检测 standalone vs next start（支持嵌套目录）
    _remote_frontend "
      cd '${FRONTEND_DIR}/web'
      _server_js=\$(find .next/standalone -name 'server.js' -maxdepth 5 2>/dev/null | head -1)
      if command -v pm2 >/dev/null 2>&1; then
        if pm2 describe '${PM2_FRONTEND}' >/dev/null 2>&1; then
          echo '[INFO] PM2 进程已存在，使用 reload（零停机）'
          pm2 reload '${PM2_FRONTEND}' --update-env
        else
          if [[ -n \"\$_server_js\" ]]; then
            echo \"[INFO] 使用 standalone 模式启动 (PM2): \$_server_js\"
            pm2 start \"\$_server_js\" --name '${PM2_FRONTEND}' --cwd '${FRONTEND_DIR}/web'
          else
            echo '[INFO] standalone 不存在，使用 next start (PM2)'
            pm2 start \"npx next start -p ${FRONTEND_PORT}\" --name '${PM2_FRONTEND}' --cwd '${FRONTEND_DIR}/web'
          fi
        fi
        pm2 save
      else
        # no PM2: kill old process and start directly
        echo '[INFO] PM2 not found, using direct process management'
        pkill -f 'next-server|node.*standalone/server.js' 2>/dev/null || true
        sleep 1
        if [[ -n \"\$_server_js\" ]]; then
          echo \"[INFO] 使用 standalone 模式启动 (direct): \$_server_js\"
          PORT=${FRONTEND_PORT} nohup node \"\$_server_js\" > /tmp/excelmanus-web.log 2>&1 &
        else
          echo '[INFO] 使用 next start 启动 (direct)'
          nohup npx next start -p ${FRONTEND_PORT} > /tmp/excelmanus-web.log 2>&1 &
        fi
        sleep 3
        if ss -tlnp 2>/dev/null | grep -q ':${FRONTEND_PORT}'; then
          echo '[OK] frontend started on port ${FRONTEND_PORT}'
        else
          echo '[WARN] frontend may not have started, check /tmp/excelmanus-web.log'
        fi
      fi
    "
  fi
}

# ── 自动修复前端 BACKEND_ORIGIN 指向旧内网 IP ──
_auto_fix_frontend_backend_origin() {
  [[ -z "$FRONTEND_HOST" ]] && return 0 || true
  [[ "$TOPOLOGY" == "local" ]] && return 0 || true

  local fe_origin
  fe_origin=$(_remote_frontend "timeout 5 grep -E '^NEXT_PUBLIC_BACKEND_ORIGIN=' ${FRONTEND_DIR}/web/.env.local 2>/dev/null || echo __MISSING__" 2>&1 || echo "__MISSING__")

  # 没有 .env.local 或没有该变量，跳过
  if echo "$fe_origin" | grep -q '__MISSING__'; then
    return 0
  fi

  # 提取当前值
  local current_val
  current_val=$(echo "$fe_origin" | sed -n 's/^NEXT_PUBLIC_BACKEND_ORIGIN=//p' | head -1 | tr -d '[:space:]')

  # 已经是 same-origin 或空，无需修复
  if [[ -z "$current_val" || "$current_val" == "same-origin" ]]; then
    return 0
  fi

  # 检测是否指向内网 IP（RFC 1918）
  if echo "$current_val" | grep -qE '(192\.168\.|10\.|172\.(1[6-9]|2[0-9]|3[01])\.)'; then
    # 计算正确值
    local correct_val
    if [[ ${#_SITE_URLS[@]} -gt 0 ]]; then
      correct_val="same-origin"
    elif [[ -n "$BACKEND_HOST" ]]; then
      correct_val="http://${BACKEND_HOST}:${BACKEND_PORT}"
    else
      return 0
    fi

    warn "前端 BACKEND_ORIGIN 指向旧内网 IP: ${current_val}"
    info "自动修复为: ${correct_val}"
    _remote_frontend "sed -i 's|^NEXT_PUBLIC_BACKEND_ORIGIN=.*|NEXT_PUBLIC_BACKEND_ORIGIN=${correct_val}|' '${FRONTEND_DIR}/web/.env.local'" || true
    log "前端 BACKEND_ORIGIN 已自动修正"
  fi
}

_build_frontend_remote() {
  local cold_cmd=""
  if [[ "$COLD_BUILD" == true ]]; then
    warn "已启用 --cold-build：将清理远端 web/.next/cache 后再构建（低内存机器风险更高）"
    cold_cmd="rm -rf .next/cache && "
  else
    info "保留远端 .next/cache 以降低冷启动构建内存峰值。需要冷构建时请显式传 --cold-build。"
  fi

  # 自动清理干扰 Next.js 编译的旧备份目录（src.bak.* 等）
  info "清理干扰构建的旧备份目录..."
  _remote_frontend "
    cd '${FRONTEND_DIR}/web' && \
    find . -maxdepth 1 -type d \( -name 'src.bak.*' -o -name 'src.backup.*' -o -name 'src_old*' \) -exec rm -rf {} + 2>/dev/null || true
  " || true

  # 注意：不使用 | tail 管道，避免吞掉 npm run build 的退出码
  info "构建前端..."
  if _remote_frontend "
    cd '${FRONTEND_DIR}/web' && \
    export NODE_OPTIONS=\"--max-old-space-size=4096\" && \
    ${cold_cmd}npm run build 2>&1; \
    BUILD_EXIT=\$?; \
    echo \"[deploy] npm run build exit code: \$BUILD_EXIT\"; \
    exit \$BUILD_EXIT
  "; then
    return 0
  fi

  warn "默认构建失败，尝试 webpack 兜底（npm run build:webpack）..."
  _remote_frontend "
    cd '${FRONTEND_DIR}/web' && \
    export NODE_OPTIONS=\"--max-old-space-size=4096\" && \
    ${cold_cmd}npm run build:webpack 2>&1; \
    BUILD_EXIT=\$?; \
    echo \"[deploy] webpack build exit code: \$BUILD_EXIT\"; \
    exit \$BUILD_EXIT
  "
}

# ── 构建产物完整性校验（返回 0=通过，1=失败）──
_validate_frontend_build() {
  info "校验前端构建产物..."
  _remote_frontend "
    cd '${FRONTEND_DIR}/web'
    _ok=true

    # 必须有 BUILD_ID（next start 依赖）
    if [[ ! -f .next/BUILD_ID ]]; then
      echo '[FAIL] .next/BUILD_ID 缺失 — next start 无法启动'
      _ok=false
    fi

    # 必须有 routes-manifest.json（next start 依赖）
    if [[ ! -f .next/routes-manifest.json ]]; then
      echo '[FAIL] .next/routes-manifest.json 缺失 — next start 无法启动'
      _ok=false
    fi

    # standalone 可选但推荐（支持嵌套目录结构）
    _found_sjs=\$(find .next/standalone -name 'server.js' -maxdepth 5 2>/dev/null | head -1)
    if [[ -n \"\$_found_sjs\" ]]; then
      echo \"[OK] standalone server.js 存在: \$_found_sjs\"
    else
      echo '[INFO] standalone/server.js 不存在，将使用 next start 模式'
    fi

    if [[ \"\$_ok\" != true ]]; then
      echo '[FATAL] 构建产物校验失败！请确认使用 webpack 模式构建（npm run build）。'
      echo '[FATAL] 将保留当前运行版本，不执行重启。'
      exit 1
    fi
    echo '[OK] 构建产物校验通过'
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
    local rsync_ssh="ssh $(_ssh_opts "$FRONTEND_SSH_KEY_PATH")"
    run "rsync -az --partial --timeout=120 --progress -e \"$rsync_ssh\" \
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

    # 检测 server.js（支持嵌套目录结构，如 .next/standalone/excelmanus/web/server.js）
    _found_server=\$(find \"\$STAGE_DIR/.next/standalone\" -name 'server.js' -maxdepth 5 2>/dev/null | head -1)
    [[ -n \"\$_found_server\" ]] || { echo '制品缺少 .next/standalone/**/server.js'; exit 1; }
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
  --exclude='.deploy_meta.json'
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
    local key_for_host="$SSH_KEY_PATH"
    [[ "$host" == "$BACKEND_HOST" ]] && key_for_host="$BACKEND_SSH_KEY_PATH" || true
    [[ "$host" == "$FRONTEND_HOST" ]] && key_for_host="$FRONTEND_SSH_KEY_PATH" || true
    local rsync_ssh="ssh $(_ssh_opts "$key_for_host")"
    # macOS openrsync 不支持 --append-verify，自动检测
    local _rsync_extra=""
    if rsync --help 2>&1 | grep -q -- '--append-verify'; then
      _rsync_extra="--append-verify"
    fi
    run "rsync -az --partial ${_rsync_extra} --timeout=120 ${_rsync_excludes[*]} --progress -e \"$rsync_ssh\" \
      '${PROJECT_ROOT}/' '${SSH_USER}@${host}:${remote_dir}/'"
  else
    info "从 Git 仓库拉取更新到 ${label} (${host:-localhost})..."
    local key_for_host="$SSH_KEY_PATH"
    [[ "$host" == "$BACKEND_HOST" ]] && key_for_host="$BACKEND_SSH_KEY_PATH" || true
    [[ "$host" == "$FRONTEND_HOST" ]] && key_for_host="$FRONTEND_SSH_KEY_PATH" || true
    local git_cmd="
      # 自动安装 git（如果远程服务器没有）
      if ! command -v git >/dev/null 2>&1; then
        echo '[deploy] git not found, installing...'
        if command -v yum >/dev/null 2>&1; then yum install -y git >/dev/null 2>&1
        elif command -v apt-get >/dev/null 2>&1; then apt-get update -qq && apt-get install -y -qq git >/dev/null 2>&1
        elif command -v dnf >/dev/null 2>&1; then dnf install -y git >/dev/null 2>&1
        elif command -v apk >/dev/null 2>&1; then apk add git >/dev/null 2>&1
        fi
        command -v git >/dev/null 2>&1 || { echo '[FATAL] failed to install git'; exit 1; }
        echo '[deploy] git installed'
      fi
      git config --global --add safe.directory '${remote_dir}' 2>/dev/null || true
      set -e
      cd '${remote_dir}' 2>/dev/null || mkdir -p '${remote_dir}'
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
      _remote "$host" "$key_for_host" "$git_cmd"
    fi
  fi
  log "${label} 代码同步完成"
}

# ── 后端 Blue/Green 部署（零中断，需 Nginx） ──
# 使用方式：在 .env.deploy 中设置 BACKEND_BLUEGREEN=true 启用
BACKEND_BLUEGREEN="${BACKEND_BLUEGREEN:-false}"
BACKEND_CANDIDATE_PORT="${BACKEND_CANDIDATE_PORT:-8001}"
BACKEND_DRAIN_SECONDS="${BACKEND_DRAIN_SECONDS:-30}"

_deploy_backend_bluegreen() {
  step "🐍 部署后端（Blue/Green 模式）..."

  # 同步代码
  _sync_code "$BACKEND_HOST" "$BACKEND_DIR" "后端"

  # 安装依赖
  if [[ "$SKIP_DEPS" != true ]]; then
    info "安装 Python 依赖..."
    _remote_backend "
      cd '${BACKEND_DIR}' && \
      if command -v uv &>/dev/null; then \
        uv sync --all-extras -q && \
        uv pip install 'httpx[socks]' -q 2>/dev/null || true; \
      else \
        source '${VENV_DIR}/bin/activate' && \
        pip install -e '.[all]' -q && \
        pip install 'httpx[socks]' -q 2>/dev/null || true; \
      fi
    "
  fi

  # 1. 启动候选实例在影子端口
  info "启动候选实例 (port ${BACKEND_CANDIDATE_PORT})..."
  _remote_backend "
    cd '${BACKEND_DIR}'
    # 启动候选实例
    nohup ${VENV_DIR}/bin/python -m uvicorn excelmanus.api:app \
      --host 0.0.0.0 --port ${BACKEND_CANDIDATE_PORT} --log-level info \
      > /tmp/excelmanus-candidate.log 2>&1 &
    echo \$! > /tmp/excelmanus-candidate.pid
    sleep 3
  "

  # 2. 健康检查候选实例
  info "检查候选实例健康状态..."
  local candidate_ok=false
  for _bg_i in $(seq 1 12); do
    local status
    status=$(_remote_backend "curl -s --max-time 5 http://localhost:${BACKEND_CANDIDATE_PORT}/api/v1/health 2>/dev/null | python3 -c 'import sys,json; print(json.load(sys.stdin).get(\"status\",\"\"))' 2>/dev/null || echo ''" 2>&1 || echo "")
    if [[ "$status" == "ok" ]]; then
      candidate_ok=true
      break
    fi
    debug "候选实例尚未就绪 (尝试 ${_bg_i}/12)..."
    sleep 5
  done

  if [[ "$candidate_ok" != true ]]; then
    error "候选实例健康检查失败，中止 Blue/Green 部署"
    _remote_backend "
      [[ -f /tmp/excelmanus-candidate.pid ]] && kill \$(cat /tmp/excelmanus-candidate.pid) 2>/dev/null || true
      rm -f /tmp/excelmanus-candidate.pid
    " || true
    return 1
  fi
  log "候选实例健康检查通过"

  # 3. 切换 Nginx upstream 到候选端口
  info "切换 Nginx upstream 到候选端口 ${BACKEND_CANDIDATE_PORT}..."
  _remote_backend "
    NGINX_CONF=\$(find /etc/nginx -name '*.conf' -exec grep -l 'upstream backend' {} \\; 2>/dev/null | head -1)
    if [[ -z \"\$NGINX_CONF\" ]]; then
      echo '[WARN] 未找到包含 upstream backend 的 Nginx 配置'
      exit 0
    fi
    # 将 active 行注释，将 candidate 行取消注释
    sed -i 's|^\\(\\s*server.*:\\)${BACKEND_PORT}\\(.*# active\\)|# \\1${BACKEND_PORT}\\2|' \"\$NGINX_CONF\"
    sed -i 's|^\\s*#\\s*\\(server.*:\\)${BACKEND_CANDIDATE_PORT}\\(.*# candidate\\)|    \\1${BACKEND_CANDIDATE_PORT}\\2|' \"\$NGINX_CONF\"
    nginx -t 2>/dev/null && nginx -s reload
  " || warn "Nginx 切流失败（非致命，已回退）"

  # 4. 等待旧连接排空
  info "等待旧连接排空 (${BACKEND_DRAIN_SECONDS}s)..."
  sleep "${BACKEND_DRAIN_SECONDS}"

  # 5. 下线旧实例
  info "下线旧实例..."
  if [[ "$SERVICE_MANAGER" == "systemd" ]]; then
    _remote_backend "sudo systemctl stop '${PM2_BACKEND}' 2>/dev/null || true"
  else
    _remote_backend "pm2 delete '${PM2_BACKEND}' 2>/dev/null || true"
  fi

  # 6. 将候选实例注册为正式实例
  info "注册候选实例为正式服务..."
  _remote_backend "
    # 停止候选进程（由 nohup 启动）
    [[ -f /tmp/excelmanus-candidate.pid ]] && kill \$(cat /tmp/excelmanus-candidate.pid) 2>/dev/null || true
    rm -f /tmp/excelmanus-candidate.pid
    sleep 2
    # 以正式端口重启
    if command -v pm2 >/dev/null 2>&1; then
      pm2 start '${BACKEND_DIR}/${VENV_DIR}/bin/python' \
        --name '${PM2_BACKEND}' --cwd '${BACKEND_DIR}' \
        -- -m uvicorn excelmanus.api:app --host 0.0.0.0 --port ${BACKEND_PORT} --log-level info
      pm2 save
    fi
  "

  # 7. 恢复 Nginx upstream 到正式端口
  _remote_backend "
    NGINX_CONF=\$(find /etc/nginx -name '*.conf' -exec grep -l 'upstream backend' {} \\; 2>/dev/null | head -1)
    if [[ -n \"\$NGINX_CONF\" ]]; then
      sed -i 's|^\\s*#\\s*\\(server.*:\\)${BACKEND_PORT}\\(.*# active\\)|    \\1${BACKEND_PORT}\\2|' \"\$NGINX_CONF\"
      sed -i 's|^\\(\\s*server.*:\\)${BACKEND_CANDIDATE_PORT}\\(.*# candidate\\)|# \\1${BACKEND_CANDIDATE_PORT}\\2|' \"\$NGINX_CONF\"
      nginx -t 2>/dev/null && nginx -s reload
    fi
  " || true

  log "后端 Blue/Green 部署完成"
}

# ── 后端 Canary 灰度部署（按权重逐步切流） ──
# 使用方式：在 .env.deploy 中设置 BACKEND_CANARY=true 启用
BACKEND_CANARY="${BACKEND_CANARY:-false}"
CANARY_STEPS="${CANARY_STEPS:-10,50,100}"
CANARY_OBSERVE_SECONDS="${CANARY_OBSERVE_SECONDS:-60}"

_set_nginx_canary_weight() {
  local active_port="$1" candidate_port="$2" weight="$3"
  # weight: 0=全部走 active，100=全部走 candidate
  # 中间值使用 Nginx weight 参数实现近似比例
  _remote_backend "
    NGINX_CONF=\$(find /etc/nginx -name '*.conf' -exec grep -l 'upstream backend' {} \\; 2>/dev/null | head -1)
    if [[ -z \"\$NGINX_CONF\" ]]; then
      echo '[WARN] 未找到包含 upstream backend 的 Nginx 配置'
      exit 1
    fi

    if [[ ${weight} -eq 0 ]]; then
      # 全部走 active
      cat > /tmp/_upstream_backend.conf <<UPEOF
upstream backend {
    server 127.0.0.1:${active_port};          # active
    # server 127.0.0.1:${candidate_port};     # candidate
}
UPEOF
    elif [[ ${weight} -ge 100 ]]; then
      # 全部走 candidate
      cat > /tmp/_upstream_backend.conf <<UPEOF
upstream backend {
    # server 127.0.0.1:${active_port};        # active
    server 127.0.0.1:${candidate_port};        # candidate
}
UPEOF
    else
      # 按权重分流：candidate weight = weight, active weight = 100 - weight
      local aw=\$((100 - ${weight}))
      cat > /tmp/_upstream_backend.conf <<UPEOF
upstream backend {
    server 127.0.0.1:${active_port} weight=\${aw};     # active
    server 127.0.0.1:${candidate_port} weight=${weight};  # candidate (canary)
}
UPEOF
    fi

    # 替换 upstream backend 块
    python3 -c \"
import re
with open('\$NGINX_CONF') as f: content = f.read()
with open('/tmp/_upstream_backend.conf') as f: new_block = f.read()
content = re.sub(r'upstream\s+backend\s*\{[^}]*\}', new_block.strip(), content)
with open('\$NGINX_CONF', 'w') as f: f.write(content)
\" && nginx -t 2>/dev/null && nginx -s reload
    rm -f /tmp/_upstream_backend.conf
  "
}

_write_canary_state() {
  # 写入灰度状态文件供 API 读取
  local state="$1" weight="$2" step_idx="$3" total="$4"
  local canary_file="${SCRIPT_DIR}/.deploy_canary.json"
  local ts
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  cat > "$canary_file" <<CEOF
{
  "active": $([ "$state" == "active" ] && echo "true" || echo "false"),
  "current_weight": ${weight},
  "step": ${step_idx},
  "total_steps": ${total},
  "started_at": "${ts}",
  "candidate_port": ${BACKEND_CANDIDATE_PORT},
  "observe_seconds": ${CANARY_OBSERVE_SECONDS}
}
CEOF
}

_deploy_backend_canary() {
  step "🐍 部署后端（Canary 灰度模式）..."

  # 同步代码
  _sync_code "$BACKEND_HOST" "$BACKEND_DIR" "后端"

  # 安装依赖
  if [[ "$SKIP_DEPS" != true ]]; then
    info "安装 Python 依赖..."
    _remote_backend "
      cd '${BACKEND_DIR}' && \
      if command -v uv &>/dev/null; then \
        uv sync --all-extras -q && \
        uv pip install 'httpx[socks]' -q 2>/dev/null || true; \
      else \
        source '${VENV_DIR}/bin/activate' && \
        pip install -e '.[all]' -q && \
        pip install 'httpx[socks]' -q 2>/dev/null || true; \
      fi
    "
  fi

  # 1. 启动候选实例在影子端口
  info "启动候选实例 (port ${BACKEND_CANDIDATE_PORT})..."
  _remote_backend "
    cd '${BACKEND_DIR}'
    nohup ${VENV_DIR}/bin/python -m uvicorn excelmanus.api:app \
      --host 0.0.0.0 --port ${BACKEND_CANDIDATE_PORT} --log-level info \
      > /tmp/excelmanus-candidate.log 2>&1 &
    echo \$! > /tmp/excelmanus-candidate.pid
    sleep 3
  "

  # 2. 健康检查候选实例
  info "检查候选实例健康状态..."
  local candidate_ok=false
  for _ci in $(seq 1 12); do
    local cstatus
    cstatus=$(_remote_backend "curl -s --max-time 5 http://localhost:${BACKEND_CANDIDATE_PORT}/api/v1/health 2>/dev/null | python3 -c 'import sys,json; print(json.load(sys.stdin).get(\"status\",\"\"))' 2>/dev/null || echo ''" 2>&1 || echo "")
    if [[ "$cstatus" == "ok" ]]; then
      candidate_ok=true
      break
    fi
    debug "候选实例尚未就绪 (尝试 ${_ci}/12)..."
    sleep 5
  done

  if [[ "$candidate_ok" != true ]]; then
    error "候选实例健康检查失败，中止灰度部署"
    _remote_backend "
      [[ -f /tmp/excelmanus-candidate.pid ]] && kill \$(cat /tmp/excelmanus-candidate.pid) 2>/dev/null || true
      rm -f /tmp/excelmanus-candidate.pid
    " || true
    _write_canary_state "inactive" 0 0 0
    return 1
  fi
  log "候选实例健康检查通过"

  # 3. 按权重阶梯逐步切流
  IFS=',' read -ra _canary_weights <<< "$CANARY_STEPS"
  local total_steps=${#_canary_weights[@]}
  local step_idx=0

  for _cw in "${_canary_weights[@]}"; do
    _cw=$(echo "$_cw" | tr -d '[:space:]')
    step_idx=$((step_idx + 1))
    info "灰度阶段 ${step_idx}/${total_steps}: 切流 ${_cw}% 到候选实例..."

    _write_canary_state "active" "$_cw" "$step_idx" "$total_steps"

    if ! _set_nginx_canary_weight "${BACKEND_PORT}" "${BACKEND_CANDIDATE_PORT}" "$_cw"; then
      error "Nginx 灰度切流失败，回退到 0%"
      _set_nginx_canary_weight "${BACKEND_PORT}" "${BACKEND_CANDIDATE_PORT}" 0 || true
      _remote_backend "
        [[ -f /tmp/excelmanus-candidate.pid ]] && kill \$(cat /tmp/excelmanus-candidate.pid) 2>/dev/null || true
        rm -f /tmp/excelmanus-candidate.pid
      " || true
      _write_canary_state "inactive" 0 0 0
      return 1
    fi

    # 观察期：每 10 秒检查一次候选健康
    if [[ "$_cw" -lt 100 ]]; then
      info "观察 ${CANARY_OBSERVE_SECONDS}s..."
      local _obs_elapsed=0
      while [[ $_obs_elapsed -lt $CANARY_OBSERVE_SECONDS ]]; do
        sleep 10
        _obs_elapsed=$((_obs_elapsed + 10))
        local _hstatus
        _hstatus=$(_remote_backend "curl -s --max-time 5 http://localhost:${BACKEND_CANDIDATE_PORT}/api/v1/health 2>/dev/null | python3 -c 'import sys,json; print(json.load(sys.stdin).get(\"status\",\"\"))' 2>/dev/null || echo ''" 2>&1 || echo "")
        if [[ "$_hstatus" != "ok" ]]; then
          error "灰度阶段 ${step_idx}: 候选实例健康检查失败 (${_obs_elapsed}s)，回退到 0%"
          _set_nginx_canary_weight "${BACKEND_PORT}" "${BACKEND_CANDIDATE_PORT}" 0 || true
          _remote_backend "
            [[ -f /tmp/excelmanus-candidate.pid ]] && kill \$(cat /tmp/excelmanus-candidate.pid) 2>/dev/null || true
            rm -f /tmp/excelmanus-candidate.pid
          " || true
          _write_canary_state "inactive" 0 0 0
          return 1
        fi
        debug "候选健康 OK (${_obs_elapsed}/${CANARY_OBSERVE_SECONDS}s, weight=${_cw}%)"
      done
      log "灰度阶段 ${step_idx} 观察通过 (${_cw}%)"
    fi
  done

  # 4. 100% 切流完成，候选转正式（与 Blue/Green 相同）
  info "灰度完成，候选实例转为正式服务..."

  # 等待旧连接排空
  info "等待旧连接排空 (${BACKEND_DRAIN_SECONDS}s)..."
  sleep "${BACKEND_DRAIN_SECONDS}"

  # 下线旧实例
  info "下线旧实例..."
  if [[ "$SERVICE_MANAGER" == "systemd" ]]; then
    _remote_backend "sudo systemctl stop '${PM2_BACKEND}' 2>/dev/null || true"
  else
    _remote_backend "pm2 delete '${PM2_BACKEND}' 2>/dev/null || true"
  fi

  # 注册候选为正式
  _remote_backend "
    [[ -f /tmp/excelmanus-candidate.pid ]] && kill \$(cat /tmp/excelmanus-candidate.pid) 2>/dev/null || true
    rm -f /tmp/excelmanus-candidate.pid
    sleep 2
    if command -v pm2 >/dev/null 2>&1; then
      pm2 start '${BACKEND_DIR}/${VENV_DIR}/bin/python' \
        --name '${PM2_BACKEND}' --cwd '${BACKEND_DIR}' \
        -- -m uvicorn excelmanus.api:app --host 0.0.0.0 --port ${BACKEND_PORT} --log-level info
      pm2 save
    fi
  "

  # 恢复 Nginx 到正式端口
  _set_nginx_canary_weight "${BACKEND_PORT}" "${BACKEND_CANDIDATE_PORT}" 0 || true

  _write_canary_state "inactive" 0 0 0
  log "后端 Canary 灰度部署完成"
}

# ── 版本号同步（pyproject.toml → web/package.json） ──
_sync_version() {
  local target_host="$1" target_dir="$2" target_key="$3"
  info "同步版本号..."
  _remote "$target_host" "$target_key" "
    cd '${target_dir}'
    _ver=\$(python3 -c \"
import re
with open('pyproject.toml') as f:
    m = re.search(r'version\\s*=\\s*\\\"([^\\\"]+)\\\"', f.read())
    print(m.group(1) if m else '')
\" 2>/dev/null || echo '')
    if [[ -z \"\$_ver\" ]]; then
      echo '[WARN] 无法从 pyproject.toml 读取版本号，跳过同步'
      exit 0
    fi
    _pkg='web/package.json'
    if [[ -f \"\$_pkg\" ]]; then
      _cur=\$(python3 -c \"import json; print(json.load(open('\$_pkg')).get('version',''))\" 2>/dev/null || echo '')
      if [[ \"\$_cur\" != \"\$_ver\" ]]; then
        python3 -c \"
import json
with open('\$_pkg') as f: d = json.load(f)
d['version'] = '\$_ver'
with open('\$_pkg', 'w') as f: json.dump(d, f, indent=2)
print()
\" 2>/dev/null
        echo \"[OK] 版本号已同步: \$_cur → \$_ver\"
      else
        echo \"[OK] 版本号已一致: \$_ver\"
      fi
    fi
  " 2>/dev/null || warn "版本号同步失败（非致命）"
}

# ── 前端依赖预检 ──
_check_frontend_deps() {
  info "检查前端依赖环境..."
  _remote_frontend "
    cd '${FRONTEND_DIR}/web'
    _ok=true

    # 检查 node
    if ! command -v node >/dev/null 2>&1; then
      echo '[FAIL] node 未安装'
      _ok=false
    else
      echo \"[OK] node: \$(node --version)\"
    fi

    # 检查 npm
    if ! command -v npm >/dev/null 2>&1; then
      echo '[FAIL] npm 未安装'
      _ok=false
    else
      echo \"[OK] npm: \$(npm --version)\"
    fi

    # 检查 node_modules 是否存在且 next 可用
    if [[ ! -d node_modules ]]; then
      echo '[WARN] node_modules 不存在，将在部署时安装'
    elif ! npx next --version >/dev/null 2>&1; then
      echo '[WARN] next 命令不可用（node_modules 可能不完整），将在部署时重新安装'
    else
      echo \"[OK] next: \$(npx next --version 2>/dev/null)\"
    fi

    if [[ \"\$_ok\" != true ]]; then
      echo '[FATAL] 前端基础环境缺失，请先安装 Node.js'
      exit 1
    fi
  "
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
      if command -v uv &>/dev/null; then \
        uv sync --all-extras -q && \
        uv pip install 'httpx[socks]' -q 2>/dev/null || true; \
      else \
        source '${VENV_DIR}/bin/activate' && \
        pip install -e '.[all]' -q && \
        pip install 'httpx[socks]' -q 2>/dev/null || true; \
      fi
    "
  fi

  # 重启后端
  info "重启后端服务..."
  if [[ "$SERVICE_MANAGER" == "systemd" ]]; then
    _remote_backend "sudo systemctl restart '${PM2_BACKEND}' 2>/dev/null || { \
      echo 'systemd 服务不存在，尝试创建...'; \
      sudo tee /etc/systemd/system/${PM2_BACKEND}.service > /dev/null <<SVCEOF
[Unit]
Description=ExcelManus Backend API
After=network.target

[Service]
Type=simple
WorkingDirectory=${BACKEND_DIR}
EnvironmentFile=-${BACKEND_DIR}/.env
ExecStart=${BACKEND_DIR}/${VENV_DIR}/bin/python -c 'import uvicorn; uvicorn.run("excelmanus.api:app", host="0.0.0.0", port=${BACKEND_PORT}, log_level="info")'
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
SVCEOF
      sudo systemctl daemon-reload && \
      sudo systemctl enable '${PM2_BACKEND}' && \
      sudo systemctl start '${PM2_BACKEND}'; }"
  else
    _remote_backend "
      if pm2 describe '${PM2_BACKEND}' >/dev/null 2>&1; then
        pm2 reload '${PM2_BACKEND}' --update-env
      else
        pm2 start '${BACKEND_DIR}/${VENV_DIR}/bin/python' \
          --name '${PM2_BACKEND}' --cwd '${BACKEND_DIR}' \
          -- -m uvicorn excelmanus.api:app --host 0.0.0.0 --port ${BACKEND_PORT} --log-level info
      fi
      pm2 save
    "
  fi
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

  # 自动检测并修复前端 NEXT_PUBLIC_BACKEND_ORIGIN 指向旧内网 IP
  _auto_fix_frontend_backend_origin

  if [[ "$SKIP_BUILD" == true ]]; then
    info "跳过构建，仅重启..."
    _ensure_frontend_standalone_assets
    _restart_frontend_service
  else
    # 安装依赖
    if [[ "$SKIP_DEPS" != true ]]; then
      info "安装前端依赖..."
      _remote_frontend "
        cd '${FRONTEND_DIR}/web' && \
        npm ci 2>&1
      "
    fi

    warn "当前为远端现场构建路径。低内存服务器建议使用 --frontend-artifact。"
    if ! _build_frontend_remote; then
      error "前端构建失败！保留当前运行版本，不执行重启。"
      return 1
    fi

    # 校验构建产物完整性（不通过则不重启，避免 502）
    if ! _validate_frontend_build; then
      error "构建产物校验失败！保留当前运行版本，不执行重启。"
      warn "请确认使用 webpack 模式构建。"
      warn "解决方法：在服务器上手动执行 cd ${FRONTEND_DIR}/web && npm run build"
      return 1
    fi

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
    return 0
  fi

  step "🔍 验证部署..."
  info "等待服务启动..."
  sleep 5

  local attempts=0
  local max_attempts=$(( VERIFY_TIMEOUT / 5 ))
  [[ $max_attempts -lt 1 ]] && max_attempts=1 || true

  while [[ $attempts -lt $max_attempts ]]; do
    local status
    status=$(curl -s --max-time 10 "$HEALTH_URL" 2>/dev/null \
      | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null \
      || echo "")

    if [[ "$status" == "ok" ]]; then
      log "部署验证通过！服务正常运行"
      if [[ ${#_SITE_URLS[@]} -gt 0 ]]; then
        for _su in "${_SITE_URLS[@]}"; do info "访问地址: ${_su}"; done
      fi
      return 0
    fi

    attempts=$((attempts + 1))
    [[ $attempts -lt $max_attempts ]] && sleep 5 || true
  done

  warn "健康检查未通过（${HEALTH_URL}）"
  warn "请检查日志:"
  if [[ "$SERVICE_MANAGER" == "systemd" ]]; then
    [[ -n "$BACKEND_HOST" ]]  && warn "  后端: ssh ${SSH_USER}@${BACKEND_HOST} 'journalctl -u ${PM2_BACKEND} --lines 20 --no-pager'" || true
    [[ -n "$FRONTEND_HOST" ]] && warn "  前端: ssh ${SSH_USER}@${FRONTEND_HOST} 'journalctl -u ${PM2_FRONTEND} --lines 20 --no-pager'" || true
  else
    [[ -n "$BACKEND_HOST" ]]  && warn "  后端: ssh ${SSH_USER}@${BACKEND_HOST} 'pm2 logs ${PM2_BACKEND} --lines 20 --nostream'" || true
    [[ -n "$FRONTEND_HOST" ]] && warn "  前端: ssh ${SSH_USER}@${FRONTEND_HOST} 'pm2 logs ${PM2_FRONTEND} --lines 20 --nostream'" || true
  fi
  return 1
}

# ── 打印配置摘要 ──
_print_summary() {
  [[ "$QUIET" == true ]] && return || true

  echo ""
  echo -e "${BOLD}══════════════════════════════════════${NC}"
  echo -e "${BOLD}  ExcelManus Deploy v${VERSION}${NC}"
  echo -e "${BOLD}══════════════════════════════════════${NC}"
  echo ""
  echo -e "  本地 OS:  ${CYAN}$(uname -s) $(uname -m)${NC}"
  [[ "$OS_TYPE" == "linux" && -n "$DISTRO_NAME" ]] && echo -e "  发行版:   ${CYAN}${DISTRO_NAME}${NC}" || true
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

  echo -e "  代码来源: ${CYAN}$([ "$FROM_LOCAL" == true ] && echo "本地 rsync" || echo "Git (${REPO_BRANCH})")${NC}"
  [[ -n "$FRONTEND_ARTIFACT" ]] && echo -e "  前端制品: ${CYAN}${FRONTEND_ARTIFACT}${NC}" || true
  [[ "$COLD_BUILD" == true ]] && echo -e "  构建缓存: ${YELLOW}冷构建 (--cold-build)${NC}" || true
  [[ -n "$FRONTEND_ARTIFACT" ]] && echo -e "  回滚备份: ${CYAN}保留最近 ${FRONTEND_RELEASE_KEEP} 个${NC}" || true
  [[ "$SKIP_BUILD" == true ]] && echo -e "  构建:     ${YELLOW}跳过${NC}" || true
  [[ "$SKIP_DEPS" == true ]]  && echo -e "  依赖:     ${YELLOW}跳过${NC}" || true
  [[ "$DRY_RUN" == true ]]    && echo -e "  ${YELLOW}⚠️  DRY RUN 模式${NC}" || true
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
    for _key_path in "$BACKEND_SSH_KEY_PATH" "$FRONTEND_SSH_KEY_PATH"; do
      if [[ -n "$_key_path" && ! -f "$_key_path" ]]; then
        error "SSH 私钥不存在: $_key_path"
        exit 1
      fi
      [[ -n "$_key_path" ]] && chmod 600 "$_key_path" 2>/dev/null || true
    done

    # 检查目标服务器可达性
    if [[ "$MODE" != "frontend" && -n "$BACKEND_HOST" ]]; then
      debug "检查后端服务器连通性..."
      if ! ssh $(_ssh_opts "$BACKEND_SSH_KEY_PATH") -o BatchMode=yes "${SSH_USER}@${BACKEND_HOST}" "echo ok" &>/dev/null; then
        error "无法连接后端服务器: ${SSH_USER}@${BACKEND_HOST}"
        exit 1
      fi
    fi
    if [[ "$MODE" != "backend" && -n "$FRONTEND_HOST" && "$FRONTEND_HOST" != "$BACKEND_HOST" ]]; then
      debug "检查前端服务器连通性..."
      if ! ssh $(_ssh_opts "$FRONTEND_SSH_KEY_PATH") -o BatchMode=yes "${SSH_USER}@${FRONTEND_HOST}" "echo ok" &>/dev/null; then
        error "无法连接前端服务器: ${SSH_USER}@${FRONTEND_HOST}"
        exit 1
      fi
    fi
  fi
}

# ── 部署锁 ──
_acquire_lock() {
  [[ "$NO_LOCK" == true || "$DRY_RUN" == true ]] && return 0 || true
  LOCK_FILE="${SCRIPT_DIR}/.deploy.lock"
  if [[ -f "$LOCK_FILE" ]]; then
    local lock_pid lock_time
    lock_pid=$(head -1 "$LOCK_FILE" 2>/dev/null || echo "")
    lock_time=$(tail -1 "$LOCK_FILE" 2>/dev/null || echo "")
    # 检查持锁进程是否还活着
    if [[ -n "$lock_pid" ]] && kill -0 "$lock_pid" 2>/dev/null; then
      error "另一个部署正在进行中 (PID: ${lock_pid}, 开始于: ${lock_time})"
      error "如需强制部署，请删除 ${LOCK_FILE} 或使用 --no-lock"
      exit 1
    else
      warn "发现过期的锁文件（进程 ${lock_pid} 已不存在），清理中..."
      rm -f "$LOCK_FILE"
    fi
  fi
  echo "$$" > "$LOCK_FILE"
  echo "$(date '+%Y-%m-%d %H:%M:%S')" >> "$LOCK_FILE"
}

_release_lock() {
  [[ -n "$LOCK_FILE" && -f "$LOCK_FILE" ]] && rm -f "$LOCK_FILE" || true
}

# ── 信号处理 ──
_cleanup_on_exit() {
  local exit_code=$?
  _release_lock
  if [[ $exit_code -ne 0 && -n "$DEPLOY_START_TIME" ]]; then
    local elapsed=$(( $(date +%s) - DEPLOY_START_TIME ))
    error "部署失败（耗时 ${elapsed}s），退出码: ${exit_code}"
    [[ -n "$DEPLOY_LOG_FILE" ]] && warn "详细日志: ${DEPLOY_LOG_FILE}" || true
  fi
}

# ── 部署历史 ──
_record_deploy_history() {
  local status="$1"
  local history_file="${SCRIPT_DIR}/.deploy_history"
  local elapsed=$(( $(date +%s) - DEPLOY_START_TIME ))
  local entry="$(date '+%Y-%m-%d %H:%M:%S') | ${status} | ${TOPOLOGY}/${MODE} | ${REPO_BRANCH:-local} | ${elapsed}s"
  echo "$entry" >> "$history_file"
  # 保留最近 100 条
  if [[ -f "$history_file" ]]; then
    tail -100 "$history_file" > "${history_file}.tmp" && mv "${history_file}.tmp" "$history_file"
  fi

  # 同时写入结构化 JSON 历史（供 API 和回滚面板使用）
  local json_file="${SCRIPT_DIR}/.deploy_history.json"
  local release_id
  release_id="$(date +%Y%m%dT%H%M%S)"
  local git_commit=""
  local pre_commit=""
  if [[ -n "$BACKEND_HOST" && "$TOPOLOGY" != "local" ]]; then
    git_commit=$(_remote_backend "cd '${BACKEND_DIR}' && git rev-parse --short HEAD 2>/dev/null || echo ''" 2>&1 || echo "")
    git_commit=$(echo "$git_commit" | tr -d '[:space:]')
  elif [[ "$TOPOLOGY" == "local" ]]; then
    git_commit=$(cd "${PROJECT_ROOT}" && git rev-parse --short HEAD 2>/dev/null || echo "")
  fi
  # 读取 pre_deploy_commit
  if [[ -n "$BACKEND_HOST" && "$TOPOLOGY" != "local" ]]; then
    pre_commit=$(_remote_backend "cd '${BACKEND_DIR}' && [[ -f .deploy_meta.json ]] && python3 -c \"import json; print(json.load(open('.deploy_meta.json')).get('pre_deploy_commit',''))\" 2>/dev/null || echo ''" 2>&1 || echo "")
    pre_commit=$(echo "$pre_commit" | tr -d '[:space:]')
  fi

  local ts
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  local json_entry="{\"release_id\":\"${release_id}\",\"timestamp\":\"${ts}\",\"status\":\"${status}\",\"topology\":\"${TOPOLOGY}\",\"mode\":\"${MODE}\",\"branch\":\"${REPO_BRANCH:-local}\",\"duration_s\":${elapsed},\"git_commit\":\"${git_commit}\",\"pre_deploy_commit\":\"${pre_commit}\"}"

  if [[ -f "$json_file" ]]; then
    # 追加到 JSON 数组末尾
    python3 -c "
import json, sys
try:
    with open('${json_file}') as f: arr = json.load(f)
except: arr = []
arr.append(json.loads(sys.argv[1]))
arr = arr[-100:]  # 保留最近 100 条
with open('${json_file}', 'w') as f: json.dump(arr, f, indent=2)
" "$json_entry" 2>/dev/null || echo "$json_entry" > "$json_file"
  else
    echo "[$json_entry]" > "$json_file"
  fi
}

# ── 部署元数据（.deploy_meta.json）──
_record_pre_deploy_commit() {
  # 记录部署前的 git commit，供回滚时精确回退
  if [[ "$MODE" != "frontend" && -n "$BACKEND_HOST" ]]; then
    _remote_backend "
      cd '${BACKEND_DIR}' && \
      _commit=\$(git rev-parse HEAD 2>/dev/null || echo '') && \
      if [[ -n \"\$_commit\" ]]; then
        if [[ -f .deploy_meta.json ]]; then
          python3 -c \"
import json, sys
d = json.load(open('.deploy_meta.json'))
d['pre_deploy_commit'] = '\$_commit'
json.dump(d, open('.deploy_meta.json', 'w'), indent=2)
\" 2>/dev/null || echo '{\"pre_deploy_commit\": \"'\$_commit'\"}' > .deploy_meta.json
        else
          echo '{\"pre_deploy_commit\": \"'\$_commit'\"}' > .deploy_meta.json
        fi
      fi
    " 2>/dev/null || true
  fi
}

_write_deploy_meta() {
  # 部署成功后写入 .deploy_meta.json
  local target_host="$1" target_dir="$2" target_key="$3"

  _remote "$target_host" "$target_key" "
    cd '${target_dir}' && \
    _commit=\$(git rev-parse --short HEAD 2>/dev/null || echo 'unknown') && \
    _full_commit=\$(git rev-parse HEAD 2>/dev/null || echo '') && \
    _pre_commit='' && \
    if [[ -f .deploy_meta.json ]]; then
      _pre_commit=\$(python3 -c \"import json; print(json.load(open('.deploy_meta.json')).get('pre_deploy_commit',''))\" 2>/dev/null || echo '')
    fi && \
    cat > .deploy_meta.json <<METAEOF
{
  \"release_id\": \"$(date +%Y%m%dT%H%M%S)\",
  \"deployed_at\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",
  \"git_commit\": \"\$_commit\",
  \"git_commit_full\": \"\$_full_commit\",
  \"pre_deploy_commit\": \"\$_pre_commit\",
  \"deploy_mode\": \"${MODE}\",
  \"topology\": \"${TOPOLOGY}\",
  \"branch\": \"${REPO_BRANCH:-unknown}\"
}
METAEOF
  " 2>/dev/null || warn "写入 .deploy_meta.json 失败（非致命）"
}

# ── Hooks ──
_run_hook() {
  local hook_name="$1" hook_script="$2"
  if [[ -n "$hook_script" ]]; then
    if [[ ! -f "$hook_script" ]]; then
      error "${hook_name} hook 脚本不存在: $hook_script"
      return 1
    fi
    step "🪝 执行 ${hook_name} hook: ${hook_script}"
    run "bash '$hook_script'"
  fi
}

# ── 前后端互联检测（通用函数，check 和 deploy 都用） ──
_check_cross_connectivity() {
  local label="$1"  # "检查" 或 "部署后验证"
  local conn_ok=true

  # 仅在有远程服务器时检测
  if [[ "$TOPOLOGY" == "local" || "$TOPOLOGY" == "docker" ]]; then
    return 0
  fi

  echo -e "\n${BOLD}${label}: 前后端互联检测${NC}"

  # 1) 前端服务器 → 后端 health API
  if [[ -n "$FRONTEND_HOST" && -n "$BACKEND_HOST" ]]; then
    local backend_url="http://${BACKEND_HOST}:${BACKEND_PORT}/api/v1/health"
    info "前端(${FRONTEND_HOST}) → 后端(${BACKEND_HOST}:${BACKEND_PORT})..."
    local fe_to_be
    fe_to_be=$(_remote_frontend "curl -s --max-time 10 ${backend_url} 2>/dev/null || echo __UNREACHABLE__" 2>&1 || echo "__UNREACHABLE__")
    if echo "$fe_to_be" | grep -q '"status"'; then
      log "前端 → 后端: 连通（${backend_url}）"
    elif echo "$fe_to_be" | grep -q '__UNREACHABLE__'; then
      warn "前端 → 后端: 不可达（${backend_url}）"
      warn "  可能原因: 后端未启动 / 防火墙未放行 ${BACKEND_PORT} / 后端未监听 0.0.0.0"
      conn_ok=false
    else
      warn "前端 → 后端: 响应异常（${fe_to_be}）"
      conn_ok=false
    fi
  fi

  # 2) 后端服务器 → 前端（反向检测，可选）
  if [[ -n "$BACKEND_HOST" && -n "$FRONTEND_HOST" && "$TOPOLOGY" == "split" ]]; then
    local frontend_url="http://${FRONTEND_HOST}:${FRONTEND_PORT}"
    info "后端(${BACKEND_HOST}) → 前端(${FRONTEND_HOST}:${FRONTEND_PORT})..."
    local be_to_fe
    be_to_fe=$(_remote_backend "curl -s --max-time 10 -o /dev/null -w %{http_code} ${frontend_url} 2>/dev/null || echo 000" 2>&1 || echo "000")
    if [[ "$be_to_fe" =~ ^(200|301|302|304)$ ]]; then
      log "后端 → 前端: 连通（HTTP ${be_to_fe}）"
    else
      warn "后端 → 前端: 不可达或异常（HTTP ${be_to_fe}）"
      warn "  前端可能尚未启动，这不影响核心功能"
    fi
  fi

  # 3) 检查后端 CORS 配置是否包含前端域名（加超时防挂起）
  if [[ -n "$BACKEND_HOST" && ${#_SITE_URLS[@]} -gt 0 ]]; then
    info "检查后端 CORS 配置..."
    local cors_check
    cors_check=$(_remote_backend "timeout 5 grep -i CORS_ALLOW_ORIGINS ${BACKEND_DIR}/.env 2>/dev/null || echo __NO_CORS__" 2>&1 || echo "__NO_CORS__")
    if echo "$cors_check" | grep -q '__NO_CORS__'; then
      warn "后端 .env 中未找到 EXCELMANUS_CORS_ALLOW_ORIGINS 配置"
      warn "  如果前端通过浏览器直连后端，需要配置 CORS 允许前端域名"
    else
      for _su in "${_SITE_URLS[@]}"; do
        if echo "$cors_check" | grep -qi "$_su"; then
          log "CORS 配置包含 ${_su}"
        else
          warn "CORS 配置可能未包含前端域名 ${_su}"
          warn "  当前配置: $(echo "$cors_check" | head -1)"
        fi
      done
    fi
  fi

  # 4) 检查 OAuth 回调白名单配置
  if [[ -n "$BACKEND_HOST" && ${#_SITE_URLS[@]} -gt 0 ]]; then
    info "检查 OAuth 回调白名单..."
    local oauth_check
    oauth_check=$(_remote_backend "timeout 5 grep -i ALLOWED_OAUTH_ORIGINS ${BACKEND_DIR}/.env 2>/dev/null || echo __NO_OAUTH__" 2>&1 || echo "__NO_OAUTH__")
    if echo "$oauth_check" | grep -q '__NO_OAUTH__'; then
      info "未设置 EXCELMANUS_ALLOWED_OAUTH_ORIGINS（将自动从 CORS origins 派生）"
    else
      for _su in "${_SITE_URLS[@]}"; do
        if echo "$oauth_check" | grep -qi "$_su"; then
          log "OAuth 白名单包含 ${_su}"
        else
          warn "OAuth 白名单可能未包含前端域名 ${_su}"
          warn "  当前配置: $(echo "$oauth_check" | head -1)"
          warn "  OAuth 登录可能无法正确跳转回 ${_su}"
        fi
      done
    fi
  fi

  # 5) 检查前端 BACKEND_ORIGIN 配置（加超时防挂起）
  if [[ -n "$FRONTEND_HOST" ]]; then
    info "检查前端 BACKEND_ORIGIN 配置..."
    local fe_backend_origin
    fe_backend_origin=$(_remote_frontend "timeout 5 grep -iE 'NEXT_PUBLIC_BACKEND_ORIGIN|BACKEND_INTERNAL_URL' ${FRONTEND_DIR}/web/.env.local ${FRONTEND_DIR}/web/.env 2>/dev/null || echo __NO_ORIGIN__" 2>&1 || echo "__NO_ORIGIN__")
    if echo "$fe_backend_origin" | grep -q '__NO_ORIGIN__'; then
      info "前端未设置 BACKEND_ORIGIN（将使用默认回退: http://{hostname}:${BACKEND_PORT}）"
    else
      log "前端后端指向: $(echo "$fe_backend_origin" | head -1)"
      # 检测是否指向旧内网 IP
      if echo "$fe_backend_origin" | grep -qE '(192\.168\.|10\.|172\.(1[6-9]|2[0-9]|3[01])\.)'; then
        warn "前端 BACKEND_ORIGIN 指向内网 IP，浏览器无法访问！"
        warn "  建议运行 deploy 命令自动修复，或手动设为 same-origin"
      fi
    fi
  fi

  if [[ "$conn_ok" != true ]]; then
    warn "前后端互联存在问题，部署后可能无法正常工作"
    return 1
  fi
  return 0
}

# ── 命令: init-env ──
_cmd_init_env() {
  step "📝 初始化远程 .env 配置..."

  local env_template="${PROJECT_ROOT}/.env.example"
  if [[ ! -f "$env_template" ]]; then
    error "未找到 .env.example 模板: $env_template"
    return 1
  fi

  # 后端 .env
  if [[ "$MODE" != "frontend" && -n "$BACKEND_HOST" ]]; then
    local be_env_path="${BACKEND_DIR}/.env"
    local be_env_exists
    be_env_exists=$(_remote_backend "[[ -f '${be_env_path}' ]] && echo 'exists' || echo 'missing'" 2>&1 || echo "missing")

    if echo "$be_env_exists" | grep -q 'exists'; then
      if [[ "$FORCE" != true ]]; then
        warn "后端 ${be_env_path} 已存在，跳过（使用 --force 覆盖）"
      else
        warn "后端 ${be_env_path} 已存在，--force 覆盖中..."
        _remote_backend "cp '${be_env_path}' '${be_env_path}.bak.$(date +%Y%m%dT%H%M%S)'" || true
        _push_env_to_backend
      fi
    else
      _push_env_to_backend
    fi
  fi

  # 前端 .env.local
  if [[ "$MODE" != "backend" && -n "$FRONTEND_HOST" ]]; then
    local fe_env_path="${FRONTEND_DIR}/web/.env.local"
    local fe_env_exists
    fe_env_exists=$(_remote_frontend "[[ -f '${fe_env_path}' ]] && echo 'exists' || echo 'missing'" 2>&1 || echo "missing")

    if echo "$fe_env_exists" | grep -q 'exists'; then
      if [[ "$FORCE" != true ]]; then
        warn "前端 ${fe_env_path} 已存在，跳过（使用 --force 覆盖）"
      else
        warn "前端 ${fe_env_path} 已存在，--force 覆盖中..."
        _remote_frontend "cp '${fe_env_path}' '${fe_env_path}.bak.$(date +%Y%m%dT%H%M%S)'" || true
        _push_env_to_frontend
      fi
    else
      _push_env_to_frontend
    fi
  fi

  echo ""
  log "init-env 完成"
  info "请登录远程服务器编辑 .env 文件，填入真实的 API Key 等配置"
  [[ -n "$BACKEND_HOST" ]]  && info "  后端: ssh ${SSH_USER}@${BACKEND_HOST} 'vi ${BACKEND_DIR}/.env'" || true
  [[ -n "$FRONTEND_HOST" ]] && info "  前端: ssh ${SSH_USER}@${FRONTEND_HOST} 'vi ${FRONTEND_DIR}/web/.env.local'" || true
}

_push_env_to_backend() {
  local env_template="${PROJECT_ROOT}/.env.example"
  info "推送 .env 模板到后端 ${BACKEND_HOST}:${BACKEND_DIR}/.env ..."

  local tmp_env
  tmp_env=$(mktemp)
  cp "$env_template" "$tmp_env"

  # 自动填充已知的部署配置（支持多站点 URL）
  if [[ ${#_SITE_URLS[@]} -gt 0 ]]; then
    # 将所有站点 URL 拼接为逗号分隔字符串
    local _all_sites
    _all_sites=$(IFS=','; echo "${_SITE_URLS[*]}")
    local _cors_origins="${_all_sites},http://localhost:3000"
    sed -i.bak "s|^# EXCELMANUS_CORS_ALLOW_ORIGINS=.*|EXCELMANUS_CORS_ALLOW_ORIGINS=${_cors_origins}|" "$tmp_env"
    # OAuth 回调白名单（未显式设置时后端会自动从 CORS origins 派生，此处显式设置更清晰）
    if ! grep -q 'EXCELMANUS_ALLOWED_OAUTH_ORIGINS' "$tmp_env"; then
      echo "" >> "$tmp_env"
      echo "# OAuth 回调允许跳转的前端来源（逗号分隔，支持完整 URL 或裸域名）" >> "$tmp_env"
      echo "EXCELMANUS_ALLOWED_OAUTH_ORIGINS=${_all_sites}" >> "$tmp_env"
    else
      sed -i.bak "s|^# EXCELMANUS_ALLOWED_OAUTH_ORIGINS=.*|EXCELMANUS_ALLOWED_OAUTH_ORIGINS=${_all_sites}|" "$tmp_env"
    fi
  fi

  # 追加渠道配置（如果模板中未包含）
  if ! grep -q 'EXCELMANUS_CHANNELS' "$tmp_env"; then
    echo "" >> "$tmp_env"
    echo "# 默认启用的渠道 Bot（逗号分隔，留空禁用）" >> "$tmp_env"
    echo "EXCELMANUS_CHANNELS=${EXCELMANUS_CHANNELS}" >> "$tmp_env"
  fi

  if [[ "$TOPOLOGY" == "local" ]]; then
    run "cp '$tmp_env' '${BACKEND_DIR}/.env'"
  else
    local rsync_ssh="ssh $(_ssh_opts "$BACKEND_SSH_KEY_PATH")"
    run "rsync -az -e \"$rsync_ssh\" '$tmp_env' '${SSH_USER}@${BACKEND_HOST}:${BACKEND_DIR}/.env'"
  fi
  rm -f "$tmp_env" "${tmp_env}.bak"
  log "后端 .env 已推送"
}

_push_env_to_frontend() {
  info "推送 .env.local 模板到前端 ${FRONTEND_HOST}:${FRONTEND_DIR}/web/.env.local ..."

  local tmp_env
  tmp_env=$(mktemp)

  local backend_origin
  if [[ ${#_SITE_URLS[@]} -gt 0 ]]; then
    backend_origin="same-origin"
  elif [[ -n "$BACKEND_HOST" ]]; then
    backend_origin="http://${BACKEND_HOST}:${BACKEND_PORT}"
  else
    backend_origin="http://localhost:${BACKEND_PORT}"
  fi

  cat > "$tmp_env" <<ENVEOF
# ExcelManus 前端配置
# 由 deploy.sh init-env 自动生成于 $(date '+%Y-%m-%d %H:%M:%S')

# 后端 API 地址
# same-origin = 走 Nginx 反代（推荐生产环境）
# http://IP:PORT = 直连后端（开发/无反代场景）
NEXT_PUBLIC_BACKEND_ORIGIN=${backend_origin}

# 内部后端地址（SSR 服务端渲染用，容器/同机场景）
BACKEND_INTERNAL_URL=http://${BACKEND_HOST:-localhost}:${BACKEND_PORT}
ENVEOF

  _remote_frontend "mkdir -p '${FRONTEND_DIR}/web'" || true
  if [[ "$TOPOLOGY" == "local" ]]; then
    run "cp '$tmp_env' '${FRONTEND_DIR}/web/.env.local'"
  else
    local rsync_ssh="ssh $(_ssh_opts "$FRONTEND_SSH_KEY_PATH")"
    run "rsync -az -e \"$rsync_ssh\" '$tmp_env' '${SSH_USER}@${FRONTEND_HOST}:${FRONTEND_DIR}/web/.env.local'"
  fi
  rm -f "$tmp_env"
  log "前端 .env.local 已推送"
}

# ── 命令: check ──
_cmd_check() {
  step "🔍 检查部署环境依赖..."
  local ok=true

  # 显示本地 OS 信息
  echo -e "\n${BOLD}本地环境:${NC}"
  info "OS: $(uname -s) $(uname -m)"
  if [[ "$OS_TYPE" == "linux" ]]; then
    [[ -n "$DISTRO_NAME" ]] && info "发行版: $DISTRO_NAME" || true
    [[ -n "$PKG_MANAGER" ]] && info "包管理器: $PKG_MANAGER" || true
  fi
  info "Bash: ${BASH_VERSION}"
  info "Shell: $SHELL"

  _check_tool() {
    local name="$1" cmd="$2" required="${3:-true}" pkg_name="${4:-$2}"
    if command -v "$cmd" &>/dev/null; then
      local ver
      ver=$("$cmd" --version 2>&1 | head -1 || echo "unknown")
      log "${name}: ${ver}"
    elif [[ "$required" == true ]]; then
      error "${name}: 未安装（必需，$(_install_hint "$pkg_name")）"
      ok=false
    else
      warn "${name}: 未安装（可选，$(_install_hint "$pkg_name")）"
    fi
  }

  echo -e "\n${BOLD}本地工具:${NC}"
  _check_tool "Git"    git    true  git
  _check_tool "SSH"    ssh    true  openssh-client
  _check_tool "rsync"  rsync  true  rsync
  _check_tool "curl"   curl   true  curl
  _check_tool "Python" python3 false python3
  _check_tool "Node"   node   false nodejs
  _check_tool "Docker" docker false docker.io
  # Linux 上 lsof 非必需（有 ss 替代），macOS 原生自带
  if [[ "$OS_TYPE" == "linux" ]]; then
    if ! command -v lsof &>/dev/null && ! command -v ss &>/dev/null; then
      warn "lsof / ss: 均未安装（端口检测需要其一，$(_install_hint lsof)）"
    fi
  fi

  if [[ "$TOPOLOGY" != "local" && -n "$BACKEND_HOST" ]]; then
    echo -e "\n${BOLD}后端服务器 (${BACKEND_HOST}):${NC}"
    if ssh $(_ssh_opts "$BACKEND_SSH_KEY_PATH") -o BatchMode=yes "${SSH_USER}@${BACKEND_HOST}" "echo ok" &>/dev/null; then
      log "SSH 连接: 正常"
      # 远端 OS 检测
      _remote_backend "uname -s -m 2>/dev/null && (. /etc/os-release 2>/dev/null && echo \"Distro: \${PRETTY_NAME:-\$ID}\" || true)" || true
      _remote_backend "python3 --version 2>&1 || echo 'Python: 未安装'" || true
      _remote_backend "node --version 2>&1 || echo 'Node: 未安装'" || true
      _remote_backend "pm2 --version 2>&1 || echo 'PM2: 未安装'" || true
      _remote_backend "git --version 2>&1 || echo 'Git: 未安装'" || true
      _remote_backend "df -h '${BACKEND_DIR}' 2>/dev/null | tail -1 || echo '磁盘: 无法检查'" || true
      _remote_backend "free -h 2>/dev/null | head -2 || echo '内存: 无法检查 (非 Linux)'" || true
      # 检查后端 .env 是否存在
      local be_env_exists
      be_env_exists=$(_remote_backend "[[ -f '${BACKEND_DIR}/.env' ]] && echo 'exists' || echo 'missing'" 2>&1 || echo "missing")
      if echo "$be_env_exists" | grep -q 'exists'; then
        log "后端 .env: 存在"
      else
        warn "后端 .env: 不存在（使用 'init-env' 命令推送模板）"
      fi
    else
      error "SSH 连接: 失败"
      ok=false
    fi
  fi

  if [[ "$TOPOLOGY" == "split" && -n "$FRONTEND_HOST" && "$FRONTEND_HOST" != "$BACKEND_HOST" ]]; then
    echo -e "\n${BOLD}前端服务器 (${FRONTEND_HOST}):${NC}"
    if ssh $(_ssh_opts "$FRONTEND_SSH_KEY_PATH") -o BatchMode=yes "${SSH_USER}@${FRONTEND_HOST}" "echo ok" &>/dev/null; then
      log "SSH 连接: 正常"
      _remote_frontend "uname -s -m 2>/dev/null && (. /etc/os-release 2>/dev/null && echo \"Distro: \${PRETTY_NAME:-\$ID}\" || true)" || true
      _remote_frontend "node --version 2>&1 || echo 'Node: 未安装'" || true
      _remote_frontend "npm --version 2>&1 || echo 'npm: 未安装'" || true
      _remote_frontend "pm2 --version 2>&1 || echo 'PM2: 未安装'" || true
      _remote_frontend "df -h '${FRONTEND_DIR}' 2>/dev/null | tail -1 || echo '磁盘: 无法检查'" || true
      _remote_frontend "free -h 2>/dev/null | head -2 || echo '内存: 无法检查 (非 Linux)'" || true
      # 检查前端 .env.local 是否存在
      local fe_env_exists
      fe_env_exists=$(_remote_frontend "[[ -f '${FRONTEND_DIR}/web/.env.local' ]] && echo 'exists' || echo 'missing'" 2>&1 || echo "missing")
      if echo "$fe_env_exists" | grep -q 'exists'; then
        log "前端 .env.local: 存在"
      else
        warn "前端 .env.local: 不存在（使用 'init-env' 命令推送模板）"
      fi
    else
      error "SSH 连接: 失败"
      ok=false
    fi
  fi

  # 前后端互联检测
  _check_cross_connectivity "检查" || ok=false

  echo ""
  if [[ "$ok" == true ]]; then
    log "环境检查通过"
  else
    error "环境检查发现问题，请修复后重试"
    return 1
  fi
}

# ── 命令: status ──
_cmd_status() {
  step "📊 部署状态..."

  if [[ "$TOPOLOGY" == "local" ]]; then
    info "本地模式"
    echo -e "  后端: $(curl -s --max-time 5 "http://localhost:${BACKEND_PORT}/api/v1/health" 2>/dev/null || echo '不可达')"
    echo -e "  前端: $(curl -s --max-time 5 "http://localhost:${FRONTEND_PORT}" -o /dev/null -w '%{http_code}' 2>/dev/null || echo '不可达')"
    return
  fi

  if [[ -n "$BACKEND_HOST" ]]; then
    echo -e "\n${BOLD}后端 (${BACKEND_HOST}):${NC}"
    if [[ "$SERVICE_MANAGER" == "systemd" ]]; then
      _remote_backend "systemctl is-active '${PM2_BACKEND}' 2>/dev/null || echo 'inactive'" || true
    else
      _remote_backend "pm2 describe '${PM2_BACKEND}' 2>/dev/null | grep -E 'status|uptime|memory' || echo '进程未找到'" || true
    fi
    _remote_backend "curl -s --max-time 5 http://localhost:${BACKEND_PORT}/api/v1/health 2>/dev/null || echo '健康检查不可达'" || true
    _remote_backend "cd '${BACKEND_DIR}' && git log -1 --format='最近提交: %h %s (%cr)' 2>/dev/null || echo 'Git: 无法获取'" || true
  fi

  if [[ -n "$FRONTEND_HOST" ]]; then
    echo -e "\n${BOLD}前端 (${FRONTEND_HOST}):${NC}"
    if [[ "$SERVICE_MANAGER" == "systemd" ]]; then
      _remote_frontend "systemctl is-active '${PM2_FRONTEND}' 2>/dev/null || echo 'inactive'" || true
    else
      _remote_frontend "pm2 describe '${PM2_FRONTEND}' 2>/dev/null | grep -E 'status|uptime|memory' || echo '进程未找到'" || true
    fi
  fi
}

# ── 命令: rollback ──
_cmd_rollback() {
  step "⏪ 回滚部署..."

  if [[ "$FORCE" != true ]]; then
    echo -e "${YELLOW}确认要回滚到上一版本吗？(y/N)${NC}"
    read -r confirm
    if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
      info "已取消"
      return 0
    fi
  fi

  local rollback_ok=true

  # 回滚前端
  if [[ "$MODE" == "full" || "$MODE" == "frontend" ]]; then
    info "回滚前端..."
    if _rollback_frontend_from_last_backup; then
      _restart_frontend_service
      log "前端回滚完成"
    else
      warn "前端回滚失败"
      rollback_ok=false
    fi
  fi

  # 回滚后端（通过 Git，优先使用 .deploy_meta.json 中的 pre_deploy_commit）
  if [[ "$MODE" == "full" || "$MODE" == "backend" ]]; then
    info "回滚后端..."
    _remote_backend "
      cd '${BACKEND_DIR}'
      _target=''
      if [[ -f .deploy_meta.json ]]; then
        _target=\$(python3 -c \"import json; print(json.load(open('.deploy_meta.json')).get('pre_deploy_commit',''))\" 2>/dev/null || echo '')
      fi
      if [[ -n \"\$_target\" ]]; then
        echo \"[INFO] 使用 .deploy_meta.json 中的 pre_deploy_commit: \$_target\"
        git log -1 --oneline
        git reset --hard \"\$_target\"
      else
        echo '[INFO] 未找到 pre_deploy_commit，回退到 HEAD~1'
        git log -1 --oneline
        git reset --hard HEAD~1
      fi
      echo '已回退到:' && git log -1 --oneline
    " || rollback_ok=false

    if [[ "$SKIP_DEPS" != true ]]; then
      info "重新安装依赖..."
      _remote_backend "
        cd '${BACKEND_DIR}' && \
        if command -v uv &>/dev/null; then \
          uv sync --all-extras -q 2>/dev/null || true; \
        else \
          source '${VENV_DIR}/bin/activate' && \
          pip install -e '.[all]' -q 2>/dev/null || true; \
        fi
      " || true
    fi

    info "重启后端服务..."
    if [[ "$SERVICE_MANAGER" == "systemd" ]]; then
      _remote_backend "sudo systemctl restart '${PM2_BACKEND}'" || rollback_ok=false
    else
      _remote_backend "pm2 restart '${PM2_BACKEND}' --update-env" || rollback_ok=false
    fi
    [[ "$rollback_ok" == true ]] && log "后端回滚完成" || warn "后端回滚可能不完整"
  fi

  _verify

  if [[ "$rollback_ok" == true ]]; then
    log "回滚完成"
  else
    error "回滚过程中出现问题，请手动检查"
    return 1
  fi
}

# ── 命令: rollback-to ──
ROLLBACK_RELEASE=""
ROLLBACK_COMMIT=""

_cmd_rollback_to() {
  step "⏪ 精确回滚..."

  if [[ -z "$ROLLBACK_RELEASE" && -z "$ROLLBACK_COMMIT" ]]; then
    error "rollback-to 需要 --release <release_id> 或 --commit <git_hash>"
    exit 1
  fi

  # 从结构化历史中查找目标 commit
  local target_commit=""
  if [[ -n "$ROLLBACK_COMMIT" ]]; then
    target_commit="$ROLLBACK_COMMIT"
    info "按 commit 回滚: ${target_commit}"
  elif [[ -n "$ROLLBACK_RELEASE" ]]; then
    local json_file="${SCRIPT_DIR}/.deploy_history.json"
    if [[ ! -f "$json_file" ]]; then
      error "未找到结构化部署历史 (.deploy_history.json)，无法按 release_id 回滚"
      exit 1
    fi
    target_commit=$(python3 -c "
import json, sys
rid = sys.argv[1]
with open('${json_file}') as f: arr = json.load(f)
for entry in reversed(arr):
    if entry.get('release_id') == rid:
        c = entry.get('pre_deploy_commit') or entry.get('git_commit', '')
        print(c)
        sys.exit(0)
print('')
" "$ROLLBACK_RELEASE" 2>/dev/null || echo "")
    target_commit=$(echo "$target_commit" | tr -d '[:space:]')
    if [[ -z "$target_commit" ]]; then
      error "未在部署历史中找到 release_id=${ROLLBACK_RELEASE} 的记录"
      exit 1
    fi
    info "按 release_id 回滚: ${ROLLBACK_RELEASE} → commit ${target_commit}"
  fi

  if [[ "$FORCE" != true ]]; then
    echo -e "${YELLOW}确认要回滚到 commit ${target_commit}？(y/N)${NC}"
    read -r confirm
    if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
      info "已取消"
      return 0
    fi
  fi

  DEPLOY_START_TIME=$(date +%s)
  local rollback_ok=true

  # 回滚前端
  if [[ "$MODE" == "full" || "$MODE" == "frontend" ]]; then
    info "回滚前端..."
    if _rollback_frontend_from_last_backup; then
      _restart_frontend_service
      log "前端回滚完成"
    else
      warn "前端回滚失败"
      rollback_ok=false
    fi
  fi

  # 回滚后端（精确 commit）
  if [[ "$MODE" == "full" || "$MODE" == "backend" ]]; then
    info "回滚后端到 commit ${target_commit}..."
    _remote_backend "
      cd '${BACKEND_DIR}'
      git log -1 --oneline
      git reset --hard '${target_commit}'
      echo '已回退到:' && git log -1 --oneline
    " || rollback_ok=false

    if [[ "$SKIP_DEPS" != true ]]; then
      info "重新安装依赖..."
      _remote_backend "
        cd '${BACKEND_DIR}' && \
        if command -v uv &>/dev/null; then \
          uv sync --all-extras -q 2>/dev/null || true; \
        else \
          source '${VENV_DIR}/bin/activate' && \
          pip install -e '.[all]' -q 2>/dev/null || true; \
        fi
      " || true
    fi

    info "重启后端服务..."
    if [[ "$SERVICE_MANAGER" == "systemd" ]]; then
      _remote_backend "sudo systemctl restart '${PM2_BACKEND}'" || rollback_ok=false
    else
      _remote_backend "pm2 restart '${PM2_BACKEND}' --update-env" || rollback_ok=false
    fi
    [[ "$rollback_ok" == true ]] && log "后端回滚完成" || warn "后端回滚可能不完整"
  fi

  _verify
  _record_deploy_history "ROLLBACK_TO"

  if [[ "$rollback_ok" == true ]]; then
    log "精确回滚完成"
  else
    error "回滚过程中出现问题，请手动检查"
    return 1
  fi
}

# ── 命令: history ──
_cmd_history() {
  local history_file="${SCRIPT_DIR}/.deploy_history"
  if [[ ! -f "$history_file" ]]; then
    info "暂无部署历史"
    return
  fi
  step "📜 部署历史（最近 20 条）"
  echo -e "${BOLD}时间                    | 状态     | 拓扑/模式      | 分支     | 耗时${NC}"
  echo "────────────────────────┼──────────┼────────────────┼──────────┼──────"
  tail -20 "$history_file"
}

# ── 命令: logs ──
_cmd_logs() {
  local log_dir="${SCRIPT_DIR}/.deploy_logs"
  if [[ ! -d "$log_dir" ]]; then
    info "暂无部署日志"
    return
  fi
  local latest
  latest=$(ls -1t "${log_dir}"/deploy_*.log 2>/dev/null | head -1 || true)
  if [[ -z "$latest" ]]; then
    info "暂无部署日志"
    return
  fi
  step "📋 最近部署日志: $(basename "$latest")"
  cat "$latest"
}

# ── 主流程 ──
main() {
  _load_config
  _parse_args "$@"
  _apply_defaults

  # 非部署命令直接执行
  case "$COMMAND" in
    check)    _print_summary; _cmd_check;    exit $? ;;
    status)   _print_summary; _cmd_status;   exit $? ;;
    init-env) _print_summary; _preflight; _cmd_init_env; exit $? ;;
    history)  _cmd_history;                  exit $? ;;
    logs)     _cmd_logs;                     exit $? ;;
    rollback)    _print_summary; _preflight; _cmd_rollback;    exit $? ;;
    rollback-to) _print_summary; _preflight; _cmd_rollback_to; exit $? ;;
  esac

  # 以下为 deploy 命令
  _init_log_file
  _print_summary
  _preflight

  trap _cleanup_on_exit EXIT
  _acquire_lock
  DEPLOY_START_TIME=$(date +%s)

  _run_hook "pre-deploy" "$PRE_DEPLOY_HOOK"

  # 记录部署前 commit（供回滚精确回退）
  _record_pre_deploy_commit

  case "$TOPOLOGY" in
    docker)
      _deploy_docker
      ;;
    *)
      # 前端依赖预检（非 backend-only 模式）
      if [[ "$MODE" == "full" || "$MODE" == "frontend" ]]; then
        _check_frontend_deps
      fi

      if [[ "$MODE" == "full" || "$MODE" == "backend" ]]; then
        if [[ "$BACKEND_CANARY" == true ]]; then
          _deploy_backend_canary
        elif [[ "$BACKEND_BLUEGREEN" == true ]]; then
          _deploy_backend_bluegreen
        else
          _deploy_backend
        fi
      fi

      # 版本号同步（代码同步后、构建前）
      if [[ "$MODE" == "full" || "$MODE" == "frontend" ]]; then
        local _ver_host _ver_dir _ver_key
        if [[ "$TOPOLOGY" == "split" ]]; then
          _ver_host="$FRONTEND_HOST"; _ver_dir="$FRONTEND_DIR"; _ver_key="$FRONTEND_SSH_KEY_PATH"
        else
          _ver_host="$BACKEND_HOST"; _ver_dir="$BACKEND_DIR"; _ver_key="$BACKEND_SSH_KEY_PATH"
        fi
        _sync_version "$_ver_host" "$_ver_dir" "$_ver_key"
      fi

      if [[ "$MODE" == "full" || "$MODE" == "frontend" ]]; then _deploy_frontend; fi
      ;;
  esac

  # 健康检查 + 失败自动回滚
  if ! _verify; then
    warn "健康检查失败，尝试自动回滚..."
    if [[ "$MODE" == "full" || "$MODE" == "backend" ]]; then
      _remote_backend "
        cd '${BACKEND_DIR}'
        _target=''
        if [[ -f .deploy_meta.json ]]; then
          _target=\$(python3 -c \"import json; print(json.load(open('.deploy_meta.json')).get('pre_deploy_commit',''))\" 2>/dev/null || echo '')
        fi
        if [[ -n \"\$_target\" ]]; then
          git reset --hard \"\$_target\" 2>/dev/null
        else
          git reset --hard HEAD~1 2>/dev/null
        fi
      " || true
      if [[ "$SERVICE_MANAGER" == "systemd" ]]; then
        _remote_backend "sudo systemctl restart '${PM2_BACKEND}'" || true
      else
        _remote_backend "pm2 restart '${PM2_BACKEND}' --update-env" || true
      fi
    fi
    if [[ "$MODE" == "full" || "$MODE" == "frontend" ]]; then
      _rollback_frontend_from_last_backup || true
      _restart_frontend_service || true
    fi
    error "部署后健康检查失败，已尝试自动回滚。请手动验证服务状态。"
    _record_deploy_history "ROLLBACK"
    _release_lock
    exit 1
  fi
  _check_cross_connectivity "部署后验证" || warn "前后端互联检测未完全通过，请检查配置"

  # 写入部署元数据（.deploy_meta.json）
  if [[ "$MODE" != "frontend" && -n "$BACKEND_HOST" ]]; then
    _write_deploy_meta "$BACKEND_HOST" "$BACKEND_DIR" "$BACKEND_SSH_KEY_PATH"
  fi
  if [[ "$TOPOLOGY" == "split" && "$MODE" != "backend" && -n "$FRONTEND_HOST" ]]; then
    _write_deploy_meta "$FRONTEND_HOST" "$FRONTEND_DIR" "$FRONTEND_SSH_KEY_PATH"
  fi

  _run_hook "post-deploy" "$POST_DEPLOY_HOOK"

  local elapsed=$(( $(date +%s) - DEPLOY_START_TIME ))
  _record_deploy_history "SUCCESS"
  _release_lock

  echo ""
  echo -e "${BOLD}══════════════════════════════════════${NC}"
  echo -e "${BOLD}  部署完成 (${elapsed}s)${NC}"
  echo -e "${BOLD}══════════════════════════════════════${NC}"
  [[ -n "$DEPLOY_LOG_FILE" ]] && info "日志: ${DEPLOY_LOG_FILE}" || true
}

main "$@"
