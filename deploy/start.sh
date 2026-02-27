#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
#  ExcelManus 一键启动脚本
#  同时启动 FastAPI 后端 + Next.js 前端（开发或生产模式）
#
#  用法:  ./deploy/start.sh [选项]
#
#  选项:
#    --production, --prod   生产模式（npm run start 代替 npm run dev）
#    --backend-only         仅启动后端
#    --frontend-only        仅启动前端
#    --backend-port PORT    后端端口（默认 8000）
#    --frontend-port PORT   前端端口（默认 3000）
#    --host HOST            后端监听地址（默认 0.0.0.0）
#    --workers N            后端 uvicorn worker 数量（默认 1）
#    --skip-deps            跳过依赖检查与自动安装
#    --no-open              不自动打开浏览器
#    --log-dir DIR          日志输出目录（默认 不写日志）
#    --health-timeout SEC   后端健康检查超时秒数（默认 30）
#    --no-kill-ports        不清理残留端口
#    -v, --verbose          详细输出
#    -h, --help             显示帮助
# ═══════════════════════════════════════════════════════════════════════

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_ROOT"

# ── 颜色 ──
if [[ -t 1 ]]; then
  GREEN='\033[0;32m'; CYAN='\033[0;36m'; RED='\033[0;31m'
  YELLOW='\033[0;33m'; BOLD='\033[1m'; NC='\033[0m'
else
  GREEN=''; CYAN=''; RED=''; YELLOW=''; BOLD=''; NC=''
fi

# ── 操作系统检测 ──
_detect_os() {
  case "$(uname -s)" in
    Darwin*)  OS_TYPE="macos" ;;
    Linux*)   OS_TYPE="linux" ;;
    MINGW*|MSYS*|CYGWIN*) OS_TYPE="windows" ;;
    *)        OS_TYPE="unknown" ;;
  esac

  # Linux 发行版与包管理器检测
  PKG_MANAGER=""
  if [[ "$OS_TYPE" == "linux" ]]; then
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

# ── 默认值 ──
PRODUCTION=false
BACKEND_ONLY=false
FRONTEND_ONLY=false
BACKEND_PORT=8000
FRONTEND_PORT=3000
BACKEND_HOST="0.0.0.0"
WORKERS=1
SKIP_DEPS=false
AUTO_OPEN=true
LOG_DIR=""
HEALTH_TIMEOUT=30
NO_KILL_PORTS=false
VERBOSE=false

# ── 日志函数 ──
_log_file=""
_log_to_file() { [[ -n "$_log_file" ]] && echo "[$(date '+%H:%M:%S')] $*" >> "$_log_file" || true; }
log()   { _log_to_file "OK  $*"; echo -e "${GREEN}✅${NC} $*"; }
info()  { _log_to_file "INF $*"; echo -e "${CYAN}ℹ️${NC}  $*"; }
warn()  { _log_to_file "WRN $*"; echo -e "${YELLOW}⚠️${NC}  $*" >&2; }
error() { _log_to_file "ERR $*"; echo -e "${RED}❌${NC} $*" >&2; }
debug() { _log_to_file "DBG $*"; [[ "$VERBOSE" == true ]] && echo -e "${CYAN}🔍${NC} $*" || true; }

# ── 解析参数 ──
_show_help() {
  sed -n '/^#  用法/,/^# ═/p' "${BASH_SOURCE[0]}" | sed 's/^# *//' | sed '$d'
  echo ""
  echo "示例:"
  echo "  ./deploy/start.sh                          # 开发模式默认启动"
  echo "  ./deploy/start.sh --prod                   # 生产模式启动"
  echo "  ./deploy/start.sh --backend-port 9000      # 自定义后端端口"
  echo "  ./deploy/start.sh --backend-only            # 仅启动后端"
  echo "  ./deploy/start.sh --log-dir ./logs          # 输出日志到文件"
  echo "  ./deploy/start.sh --workers 4 --prod        # 生产模式 4 workers"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --production|--prod)    PRODUCTION=true ;;
    --backend-only)         BACKEND_ONLY=true ;;
    --frontend-only)        FRONTEND_ONLY=true ;;
    --backend-port)         BACKEND_PORT="$2"; shift ;;
    --frontend-port)        FRONTEND_PORT="$2"; shift ;;
    --host)                 BACKEND_HOST="$2"; shift ;;
    --workers)              WORKERS="$2"; shift ;;
    --skip-deps)            SKIP_DEPS=true ;;
    --no-open)              AUTO_OPEN=false ;;
    --log-dir)              LOG_DIR="$2"; shift ;;
    --health-timeout)       HEALTH_TIMEOUT="$2"; shift ;;
    --no-kill-ports)        NO_KILL_PORTS=true ;;
    -v|--verbose)           VERBOSE=true ;;
    -h|--help)              _show_help; exit 0 ;;
    *)                      error "未知参数: $1（使用 --help 查看帮助）"; exit 1 ;;
  esac
  shift
done

# ── 互斥检查 ──
if [[ "$BACKEND_ONLY" == true && "$FRONTEND_ONLY" == true ]]; then
  error "--backend-only 与 --frontend-only 不能同时使用"
  exit 1
fi

# ── 加载 .env（如存在）──
_load_env() {
  local env_file="$1"
  if [[ -f "$env_file" ]]; then
    debug "加载环境变量: $env_file"
    set -a
    # shellcheck source=/dev/null
    source "$env_file" || {
      set +a
      echo "❌ 加载环境文件失败: $env_file（请检查语法，例如含 < > 的值需用引号包裹）" >&2
      exit 1
    }
    set +a
  fi
}
# 优先级: .env.local > .env
_load_env "${PROJECT_ROOT}/.env"
_load_env "${PROJECT_ROOT}/.env.local"

# 环境变量覆盖（优先级低于命令行参数，但高于 .env）
BACKEND_PORT="${EXCELMANUS_BACKEND_PORT:-$BACKEND_PORT}"
FRONTEND_PORT="${EXCELMANUS_FRONTEND_PORT:-$FRONTEND_PORT}"

# ── 初始化日志文件 ──
if [[ -n "$LOG_DIR" ]]; then
  mkdir -p "$LOG_DIR"
  _log_file="${LOG_DIR}/start_$(date +%Y%m%dT%H%M%S).log"
  echo "# ExcelManus Start — $(date '+%Y-%m-%d %H:%M:%S')" > "$_log_file"
  info "日志输出到: $_log_file"
fi

# ── 依赖检查 ──
_check_command() {
  local cmd="$1" label="$2" install_hint="$3"
  if ! command -v "$cmd" &>/dev/null; then
    error "未找到 $label（$cmd），请安装: $install_hint"
    return 1
  fi
  return 0
}

_check_deps() {
  local ok=true

  # Python / venv
  if [[ "$FRONTEND_ONLY" != true ]]; then
    if [[ -d ".venv" ]]; then
      local py_bin=".venv/bin/python"
      if [[ ! -x "$py_bin" ]]; then
        error ".venv 目录存在但 $py_bin 不可执行"
        ok=false
      else
        local py_ver
        py_ver=$("$py_bin" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+')
        debug "Python 版本: $py_ver"
      fi
    elif command -v uv &>/dev/null; then
      warn "未找到 .venv 虚拟环境"
      info "检测到 uv，尝试自动创建虚拟环境并安装依赖..."
      uv venv .venv && .venv/bin/python -m pip install -e . -q || {
        error "自动创建虚拟环境失败，请手动运行: uv venv && uv pip install -e ."
        ok=false
      }
    elif command -v python3 &>/dev/null; then
      warn "未找到 .venv 虚拟环境，尝试用 python3 -m venv 创建..."
      python3 -m venv .venv && .venv/bin/python -m pip install -e . -q || {
        error "自动创建虚拟环境失败，请手动运行: python3 -m venv .venv && .venv/bin/pip install -e ."
        ok=false
      }
    else
      local py_hint="uv venv && uv pip install -e ."
      if [[ "$OS_TYPE" == "linux" ]]; then
        py_hint="$(_install_hint python3-venv) && python3 -m venv .venv && .venv/bin/pip install -e ."
      fi
      error "未找到 .venv 虚拟环境，请先运行: $py_hint"
      ok=false
    fi

    # 检查 uvicorn
    if [[ -x ".venv/bin/python" ]]; then
      if ! .venv/bin/python -c "import uvicorn" 2>/dev/null; then
        warn "uvicorn 未安装，尝试自动安装..."
        .venv/bin/python -m pip install uvicorn -q 2>/dev/null || {
          error "uvicorn 安装失败"
          ok=false
        }
      fi
    fi
  fi

  # Node.js / npm
  if [[ "$BACKEND_ONLY" != true ]]; then
    local node_hint="https://nodejs.org/"
    if [[ "$OS_TYPE" == "linux" ]]; then
      node_hint="https://nodejs.org/ 或 $(_install_hint nodejs)"
    fi
    _check_command node "Node.js" "$node_hint" || ok=false
    _check_command npm "npm" "$node_hint" || ok=false

    if command -v node &>/dev/null; then
      local node_ver
      node_ver=$(node --version 2>/dev/null)
      debug "Node.js 版本: $node_ver"
    fi

    # web/node_modules
    if [[ ! -d "web/node_modules" ]]; then
      info "首次启动，安装前端依赖..."
      (cd web && npm install) || { error "npm install 失败"; ok=false; }
    fi

    # 生产模式需要先构建
    if [[ "$PRODUCTION" == true && ! -d "web/.next" ]]; then
      info "生产模式首次启动，构建前端..."
      (cd web && npm run build) || { error "npm run build 失败"; ok=false; }
    fi
  fi

  # curl（健康检查用）
  _check_command curl "curl" "$(_install_hint curl)" || {
    warn "curl 不可用，将跳过健康检查"
  }

  [[ "$ok" == true ]] || return 1
}

if [[ "$SKIP_DEPS" != true ]]; then
  _check_deps || exit 1
fi

echo -e "${GREEN}🚀 ExcelManus 启动中...${NC}"
[[ "$PRODUCTION" == true ]] && echo -e "${BOLD}   模式: 生产${NC}" || echo -e "${BOLD}   模式: 开发${NC}"
debug "OS: ${OS_TYPE} ($(uname -s) $(uname -m))${PKG_MANAGER:+ [pkg: $PKG_MANAGER]}"

# ── 清理残留端口 ──
_find_pids_on_port() {
  local port="$1"
  local pids=""
  # 方法 1: lsof（macOS 原生，Linux 需安装）
  if command -v lsof &>/dev/null; then
    pids=$(lsof -ti :"$port" 2>/dev/null || true)
  fi
  # 方法 2: ss + awk（Linux 原生，无需额外安装）
  if [[ -z "$pids" ]] && command -v ss &>/dev/null; then
    pids=$(ss -tlnp "sport = :$port" 2>/dev/null \
      | grep -oP 'pid=\K[0-9]+' 2>/dev/null || true)
  fi
  # 方法 3: fuser（Linux 备选）
  if [[ -z "$pids" ]] && command -v fuser &>/dev/null; then
    pids=$(fuser "$port/tcp" 2>/dev/null | tr -s ' ' '\n' | grep -E '^[0-9]+$' || true)
  fi
  echo "$pids"
}

_kill_port() {
  local port="$1"
  local pids
  pids=$(_find_pids_on_port "$port")
  if [[ -n "$pids" ]]; then
    warn "端口 $port 被占用 (PID $pids)，正在清理..."
    # 先 SIGTERM 优雅退出，等 2 秒后 SIGKILL
    echo "$pids" | xargs kill -15 2>/dev/null || true
    sleep 2
    # 检查是否仍存活
    local still_alive
    still_alive=$(_find_pids_on_port "$port")
    if [[ -n "$still_alive" ]]; then
      echo "$still_alive" | xargs kill -9 2>/dev/null || true
      sleep 1
    fi
  fi
}

if [[ "$NO_KILL_PORTS" != true ]]; then
  [[ "$FRONTEND_ONLY" != true ]] && _kill_port "$BACKEND_PORT"
  [[ "$BACKEND_ONLY" != true ]]  && _kill_port "$FRONTEND_PORT"
fi

# ── 进程管理 ──
BACKEND_PID=""
FRONTEND_PID=""

cleanup() {
  echo ""
  echo -e "${CYAN}🛑 正在关闭服务...${NC}"
  local pids=()
  [[ -n "$FRONTEND_PID" ]] && pids+=("$FRONTEND_PID")
  [[ -n "$BACKEND_PID" ]]  && pids+=("$BACKEND_PID")

  # 第一阶段：SIGTERM（优雅关闭）
  for pid in "${pids[@]}"; do
    kill -15 "$pid" 2>/dev/null || true
  done

  # 等待最多 5 秒
  local waited=0
  while [[ $waited -lt 5 ]]; do
    local all_done=true
    for pid in "${pids[@]}"; do
      if kill -0 "$pid" 2>/dev/null; then
        all_done=false
        break
      fi
    done
    [[ "$all_done" == true ]] && break
    sleep 1
    waited=$((waited + 1))
  done

  # 第二阶段：SIGKILL（强制终止未退出的进程）
  for pid in "${pids[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      debug "进程 $pid 未响应 SIGTERM，强制终止"
      kill -9 "$pid" 2>/dev/null || true
    fi
  done

  wait 2>/dev/null
  echo -e "${GREEN}✅ 已关闭${NC}"
  [[ -n "$_log_file" ]] && info "日志已保存到: $_log_file"
}
trap cleanup EXIT INT TERM

# ── 启动后端 ──
_start_backend() {
  info "启动 FastAPI 后端 (${BACKEND_HOST}:${BACKEND_PORT})..."

  local log_redirect=""
  if [[ -n "$LOG_DIR" ]]; then
    log_redirect=" >> ${LOG_DIR}/backend.log 2>&1"
  fi

  if [[ "$WORKERS" -gt 1 ]]; then
    eval ".venv/bin/python -c \"import uvicorn; uvicorn.run('excelmanus.api:app', host='${BACKEND_HOST}', port=${BACKEND_PORT}, log_level='info', workers=${WORKERS})\"${log_redirect}" &
  else
    eval ".venv/bin/python -c \"import uvicorn; uvicorn.run('excelmanus.api:app', host='${BACKEND_HOST}', port=${BACKEND_PORT}, log_level='info')\"${log_redirect}" &
  fi
  BACKEND_PID=$!

  # 等待后端就绪
  local ready=false
  for _ in $(seq 1 "$HEALTH_TIMEOUT"); do
    if curl -s "http://localhost:${BACKEND_PORT}/api/v1/health" >/dev/null 2>&1; then
      log "后端已就绪 (PID $BACKEND_PID)"
      ready=true
      break
    fi
    if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
      error "后端启动失败，请检查配置（.env 文件）"
      [[ -n "$LOG_DIR" ]] && error "查看日志: ${LOG_DIR}/backend.log"
      exit 1
    fi
    sleep 1
  done

  if [[ "$ready" == false ]]; then
    error "后端启动超时（${HEALTH_TIMEOUT}s）"
    exit 1
  fi
}

# ── 启动前端 ──
_start_frontend() {
  local mode_label="dev"
  local run_cmd="npm run dev -- -p ${FRONTEND_PORT}"

  if [[ "$PRODUCTION" == true ]]; then
    mode_label="start"
    run_cmd="npm run start -- -p ${FRONTEND_PORT}"
  fi

  info "启动 Next.js 前端 [${mode_label}] (端口 ${FRONTEND_PORT})..."

  if [[ -n "$LOG_DIR" ]]; then
    (cd web && exec $run_cmd >> "${LOG_DIR}/frontend.log" 2>&1) &
  else
    (cd web && exec $run_cmd) &
  fi
  FRONTEND_PID=$!
}

# ── 主流程 ──
if [[ "$FRONTEND_ONLY" != true ]]; then
  _start_backend
fi

if [[ "$BACKEND_ONLY" != true ]]; then
  _start_frontend
fi

# 等待前端启动
sleep 3

# ── 自动打开浏览器 ──
if [[ "$AUTO_OPEN" == true && "$BACKEND_ONLY" != true ]]; then
  local_url="http://localhost:${FRONTEND_PORT}"
  if command -v open &>/dev/null; then
    open "$local_url" 2>/dev/null || true
  elif command -v xdg-open &>/dev/null; then
    xdg-open "$local_url" 2>/dev/null || true
  fi
fi

# ── 启动摘要 ──
echo ""
echo -e "${GREEN}════════════════════════════════════════${NC}"
echo -e "${GREEN}  ExcelManus 已启动！${NC}"
[[ "$PRODUCTION" == true ]] && echo -e "${GREEN}  模式: 生产${NC}" || echo -e "${GREEN}  模式: 开发${NC}"
[[ "$BACKEND_ONLY" != true ]]  && echo -e "${GREEN}  前端: http://localhost:${FRONTEND_PORT}${NC}"
[[ "$FRONTEND_ONLY" != true ]] && echo -e "${GREEN}  后端: http://localhost:${BACKEND_PORT}${NC}"
[[ -n "$LOG_DIR" ]] && echo -e "${GREEN}  日志: ${LOG_DIR}/${NC}"
echo -e "${GREEN}  按 Ctrl+C 停止所有服务${NC}"
echo -e "${GREEN}════════════════════════════════════════${NC}"
echo ""

wait
