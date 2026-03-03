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
#    --update               更新到最新版本后启动
#    --check-update         仅检查是否有可用更新
#    --create-shortcut      创建桌面快捷方式
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
    --update)               bash "${SCRIPT_DIR}/update.sh" --yes && info "更新完成，继续启动..." ;;
    --check-update)         bash "${SCRIPT_DIR}/update.sh" --check; exit $? ;;
    --create-shortcut)      python3 -c "from excelmanus.shortcuts import create_desktop_shortcut; r=create_desktop_shortcut('${PROJECT_ROOT}'); print(r or '创建失败')"; exit $? ;;
    -v|--verbose)           VERBOSE=true ;;
    -h|--help)              _show_help; exit 0 ;;
    *)                      error "未知参数: $1（使用 --help 查看帮助）"; exit 1 ;;
  esac
  shift
done

# ── Git 仓库配置（优先 Gitee）──
REPO_URL="https://gitee.com/kilolonion/excelmanus.git"
REPO_URL_GITHUB="https://github.com/kilolonion/excelmanus"
REPO_BRANCH="main"

# ── 检查项目完整性（缺失则克隆，国内优先 Gitee）──
if [[ ! -f "${PROJECT_ROOT}/pyproject.toml" ]]; then
  warn "未检测到完整项目文件"
  if ! command -v git &>/dev/null; then
    error "未找到 Git，请先安装: $(_install_hint git)"
    error "或手动下载项目: $REPO_URL"
    exit 1
  fi
  tmpdir="${PROJECT_ROOT}_tmp"
  clone_ok=false
  # Try Gitee first
  info "正在从 Gitee 克隆项目..."
  git clone --depth 1 -b "$REPO_BRANCH" "$REPO_URL" "$tmpdir" 2>/dev/null && clone_ok=true
  if [[ "$clone_ok" != true ]]; then
    info "Gitee 克隆失败，尝试 GitHub..."
    git clone --depth 1 -b "$REPO_BRANCH" "$REPO_URL_GITHUB" "$tmpdir" || {
      error "Git 克隆失败，请检查网络连接"
      exit 1
    }
  fi
  cp -a "$tmpdir"/. "$PROJECT_ROOT"/
  rm -rf "$tmpdir"
  log "项目已克隆完成"
fi

# ── 交互式 .env 配置（首次启动）──
if [[ ! -f "${PROJECT_ROOT}/.env" ]]; then
  echo ""
  echo -e "${CYAN}  ========================================${NC}"
  echo -e "${CYAN}    首次启动 - 配置 ExcelManus${NC}"
  echo -e "${CYAN}  ========================================${NC}"
  echo ""
  echo "  需要配置 LLM API 信息才能使用。"
  echo -e "  ${YELLOW}（直接按回车可跳过，稍后手动编辑 .env 文件）${NC}"
  echo ""
  read -rp "  API Key: " input_api_key
  read -rp "  Base URL (例: https://api.openai.com/v1): " input_base_url
  read -rp "  Model (例: gpt-4o): " input_model
  echo ""
  if [[ -z "$input_api_key" ]]; then
    warn "未填写 API Key，创建空模板 .env 文件"
    warn "请稍后编辑 ${PROJECT_ROOT}/.env 填入配置"
    cat > "${PROJECT_ROOT}/.env" <<EOF
# ExcelManus Configuration
# Please fill in your LLM API settings
EXCELMANUS_API_KEY=your-api-key
EXCELMANUS_BASE_URL=https://your-llm-endpoint/v1
EXCELMANUS_MODEL=your-model-id
EOF
  else
    cat > "${PROJECT_ROOT}/.env" <<EOF
# ExcelManus Configuration
EXCELMANUS_API_KEY=${input_api_key}
EXCELMANUS_BASE_URL=${input_base_url}
EXCELMANUS_MODEL=${input_model}
EOF
    log ".env 配置文件已创建"
  fi
  echo ""
fi

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

# 默认启用 QQ 渠道 Bot（可通过 .env 或环境变量覆盖）
EXCELMANUS_CHANNELS="${EXCELMANUS_CHANNELS:-qq}"
export EXCELMANUS_CHANNELS

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

# ── 网络探测：自动识别国内网络 ──
IS_DOMESTIC=false
NPM_MIRROR_REGISTRY=""
_detect_domestic_network() {
  local result
  result=$(python3 -c "
import socket, time, concurrent.futures
def ping(host):
    try:
        t=time.monotonic(); socket.create_connection((host,443),3); return time.monotonic()-t
    except: return 999
with concurrent.futures.ThreadPoolExecutor(2) as p:
    fm=p.submit(ping,'pypi.tuna.tsinghua.edu.cn')
    fp=p.submit(ping,'pypi.org')
    tm,tp=fm.result(5),fp.result(5)
print('1' if tm<5 and (tp>5 or tm<tp*0.8) else '0')
" 2>/dev/null || echo "0")
  if [[ "$result" == "1" ]]; then
    IS_DOMESTIC=true
    return 0
  fi
  return 1
}

_has_uv() {
  command -v uv &>/dev/null && return 0 || return 1
}

_init_pip_mirror() {
  # Auto-detect domestic network if not already known
  if [[ "$IS_DOMESTIC" != true ]]; then
    _detect_domestic_network && debug "检测到国内网络，启用镜像加速" || true
  fi
  if [[ "$IS_DOMESTIC" == true ]]; then
    PIP_MIRROR_URL="https://pypi.tuna.tsinghua.edu.cn/simple"
    PIP_MIRROR_HOST="pypi.tuna.tsinghua.edu.cn"
    NPM_MIRROR_REGISTRY="--registry=https://registry.npmmirror.com"
  else
    PIP_MIRROR_URL=""
    PIP_MIRROR_HOST=""
    NPM_MIRROR_REGISTRY=""
  fi
}

_pip_install() {
  # Prefer uv sync for reproducible installs from lockfile
  if _has_uv; then
    debug "使用 uv sync (加速模式)"
    local uv_args=(sync --all-extras)
    if [[ -n "$PIP_MIRROR_URL" ]]; then
      uv_args+=(--index-url "$PIP_MIRROR_URL")
    fi
    uv "${uv_args[@]}" && return 0
    warn "uv sync 失败, 尝试 uv pip install..."
    if [[ -n "$PIP_MIRROR_URL" ]]; then
      uv pip install "$@" -i "$PIP_MIRROR_URL" && return 0
    fi
    uv pip install "$@" && return 0
    warn "uv 安装失败, 回退到 pip..."
  fi
  # Fallback to pip
  if [[ -n "$PIP_MIRROR_URL" ]]; then
    .venv/bin/python -m pip install "$@" -i "$PIP_MIRROR_URL" --trusted-host "$PIP_MIRROR_HOST" && return 0
    warn "镜像安装失败, 尝试默认源..."
  fi
  .venv/bin/python -m pip install "$@"
}

_check_deps() {
  local ok=true

  # pip 镜像（默认清华，失败回退 PyPI）
  _init_pip_mirror

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
      uv sync --all-extras || {
        error "自动创建虚拟环境失败，请手动运行: uv sync --all-extras"
        ok=false
      }
    elif command -v python3 &>/dev/null; then
      warn "未找到 .venv 虚拟环境，尝试用 python3 -m venv 创建..."
      python3 -m venv .venv && _pip_install -e '.[all]' || {
        error "自动创建虚拟环境失败，请手动运行: python3 -m venv .venv && .venv/bin/pip install -e '.[all]'"
        ok=false
      }
    else
      local py_hint="uv sync --all-extras"
      if [[ "$OS_TYPE" == "linux" ]]; then
        py_hint="curl -LsSf https://astral.sh/uv/install.sh | sh && uv sync --all-extras"
      fi
      error "未找到 .venv 虚拟环境，请先运行: $py_hint"
      ok=false
    fi

    # 检查项目依赖是否已安装
    NEED_PIP=false
    if [[ -x ".venv/bin/python" ]]; then
      if ! .venv/bin/python -c "import fastapi; import uvicorn; import rich" 2>/dev/null; then
        NEED_PIP=true
      fi
    fi
  fi

  # Node.js / npm
  NEED_NPM=false
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
      NEED_NPM=true
    fi
  fi

  # ── 并行安装 pip + npm（互不依赖） ──
  if [[ "$NEED_PIP" == true ]] || [[ "$NEED_NPM" == true ]]; then
    info "正在并行安装依赖（首次启动可能需要几分钟）..."

    local pip_pid=0 npm_pid=0
    local pip_ok_f=$(mktemp) npm_ok_f=$(mktemp)

    if [[ "$NEED_PIP" == true ]]; then
      ( _pip_install -e '.[all]' && echo "1" > "$pip_ok_f" || echo "0" > "$pip_ok_f" ) &
      pip_pid=$!
    else
      echo "1" > "$pip_ok_f"
    fi

    if [[ "$NEED_NPM" == true ]]; then
      ( cd web && npm install ${NPM_MIRROR_REGISTRY:-} && echo "1" > "$npm_ok_f" || echo "0" > "$npm_ok_f" ) &
      npm_pid=$!
    else
      echo "1" > "$npm_ok_f"
    fi

    [[ $pip_pid -ne 0 ]] && wait $pip_pid 2>/dev/null
    [[ $npm_pid -ne 0 ]] && wait $npm_pid 2>/dev/null

    if [[ "$(cat "$pip_ok_f" 2>/dev/null)" != "1" ]]; then
      error "项目依赖安装失败"
      ok=false
    else
      [[ "$NEED_PIP" == true ]] && log "项目依赖已安装"
    fi

    if [[ "$(cat "$npm_ok_f" 2>/dev/null)" != "1" ]]; then
      error "npm install 失败"
      ok=false
    else
      [[ "$NEED_NPM" == true ]] && log "前端依赖已安装"
    fi

    rm -f "$pip_ok_f" "$npm_ok_f"
  fi

  if [[ "$BACKEND_ONLY" != true ]]; then
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
    # 优先使用 standalone 模式（Next.js 16 + output: "standalone"）
    if [[ -f "web/.next/standalone/server.js" ]]; then
      mode_label="standalone"
      run_cmd="node .next/standalone/server.js"
    else
      mode_label="start"
      run_cmd="npm run start -- -p ${FRONTEND_PORT}"
    fi
  fi

  info "启动 Next.js 前端 [${mode_label}] (端口 ${FRONTEND_PORT})..."

  if [[ -n "$LOG_DIR" ]]; then
    (cd web && PORT=${FRONTEND_PORT} exec $run_cmd >> "${LOG_DIR}/frontend.log" 2>&1) &
  else
    (cd web && PORT=${FRONTEND_PORT} exec $run_cmd) &
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
