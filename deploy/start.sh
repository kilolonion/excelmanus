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
#    --host HOST            后端监听地址（默认 127.0.0.1）
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
BACKEND_HOST="127.0.0.1"
WORKERS=1
SKIP_DEPS=false
AUTO_OPEN=true
LOG_DIR=""
HEALTH_TIMEOUT=30
NO_KILL_PORTS=false
VERBOSE=false
DO_UPDATE=false
DO_CHECK_UPDATE=false

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
  echo "  ./deploy/start.sh --prod                    # 生产模式（默认 1 worker）"
  echo "  ./deploy/start.sh --workers 4 --prod        # 不推荐：多 worker 会跨进程丢失信封缓存"
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
    --update)               DO_UPDATE=true ;;
    --check-update)         DO_CHECK_UPDATE=true ;;
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

# ── 互斥检查 ──
if [[ "$BACKEND_ONLY" == true && "$FRONTEND_ONLY" == true ]]; then
  error "--backend-only 与 --frontend-only 不能同时使用"
  exit 1
fi

# ── 端口（命令行优先，其次已有进程环境）──
BACKEND_PORT="${EXCELMANUS_BACKEND_PORT:-$BACKEND_PORT}"
FRONTEND_PORT="${EXCELMANUS_FRONTEND_PORT:-$FRONTEND_PORT}"

# ── 初始化日志文件 ──
if [[ -n "$LOG_DIR" ]]; then
  mkdir -p "$LOG_DIR"
  # 转为绝对路径：前端在子 shell 中 `cd web` 后再引用相对日志目录会失效（导致前端起不来）
  LOG_DIR="$(cd "$LOG_DIR" && pwd)"
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
      local node_ver node_major
      node_ver=$(node --version 2>/dev/null)
      debug "Node.js 版本: $node_ver"
      node_major=$(printf '%s' "$node_ver" | sed -E 's/^v([0-9]+).*/\1/')
      if [[ -n "$node_major" && "$node_major" -lt 20 ]]; then
        error "Node.js $node_ver 过低，Web UI 需要 ≥ 20.9（Next.js 16）。安装: $node_hint"
        ok=false
      fi
    fi

    # node_modules can survive an interrupted install. Verify the files needed
    # by Next.js startup so a partial installation is repaired automatically.
    if [[ ! -f "web/node_modules/next/package.json" ]] ||
       [[ ! -f "web/node_modules/next/dist/build/webpack/loaders/next-flight-client-entry-loader.js" ]] ||
       [[ ! -f "web/node_modules/typescript/package.json" ]]; then
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

_excelmanus_home() {
  if [[ -n "${EXCELMANUS_HOME:-}" ]]; then
    printf '%s\n' "${EXCELMANUS_HOME}"
  else
    printf '%s\n' "${HOME}/.excelmanus"
  fi
}

_python_bin() {
  if [[ -x "${PROJECT_ROOT}/.venv/bin/python" ]]; then
    echo "${PROJECT_ROOT}/.venv/bin/python"
  else
    echo "python3"
  fi
}

_write_runtime() {
  SUPERVISOR_PID=$$ \
  BACKEND_PID="${BACKEND_PID:-0}" \
  FRONTEND_PID="${FRONTEND_PID:-0}" \
  BACKEND_PORT="$BACKEND_PORT" \
  FRONTEND_PORT="$FRONTEND_PORT" \
  BACKEND_HOST="$BACKEND_HOST" \
  PRODUCTION="$PRODUCTION" \
  BACKEND_ONLY="$BACKEND_ONLY" \
  FRONTEND_ONLY="$FRONTEND_ONLY" \
  WORKERS="$WORKERS" \
  PROJECT_ROOT="$PROJECT_ROOT" \
  START_SCRIPT="${SCRIPT_DIR}/start.sh" \
  "$(_python_bin)" - <<'PY'
import json, os
from pathlib import Path
home = Path(os.environ.get("EXCELMANUS_HOME") or (Path.home() / ".excelmanus"))
home.mkdir(parents=True, exist_ok=True)

def flag(name: str) -> bool:
    return os.environ.get(name, "").lower() == "true"

def ipid(name: str) -> int:
    try:
        return int(os.environ.get(name) or 0)
    except ValueError:
        return 0

supervisor = ipid("SUPERVISOR_PID")
pgid = os.getpgid(supervisor) if supervisor and hasattr(os, "getpgid") else supervisor
payload = {
    "supervisor_pid": supervisor,
    "pgid": pgid,
    "backend_pid": ipid("BACKEND_PID"),
    "frontend_pid": ipid("FRONTEND_PID"),
    "backend_port": ipid("BACKEND_PORT") or 8000,
    "frontend_port": ipid("FRONTEND_PORT") or 3000,
    "backend_host": os.environ.get("BACKEND_HOST") or "127.0.0.1",
    "production": flag("PRODUCTION"),
    "backend_only": flag("BACKEND_ONLY"),
    "frontend_only": flag("FRONTEND_ONLY"),
    "workers": ipid("WORKERS") or 1,
    "project_root": os.environ.get("PROJECT_ROOT") or "",
    "start_script": os.environ.get("START_SCRIPT") or "",
}
(home / "runtime.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
PY
}

_clear_runtime() {
  SUPERVISOR_PID=$$ "$(_python_bin)" - <<'PY'
import json, os
from pathlib import Path
home = Path(os.environ.get("EXCELMANUS_HOME") or (Path.home() / ".excelmanus"))
path = home / "runtime.json"
if not path.is_file():
    raise SystemExit
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    raise SystemExit
if str(data.get("supervisor_pid", "")) == os.environ.get("SUPERVISOR_PID", ""):
    path.unlink(missing_ok=True)
PY
}

_api_is_running() {
  local port="${BACKEND_PORT:-8000}"
  local home rt_port
  home="$(_excelmanus_home)"
  if [[ -f "${home}/runtime.json" ]]; then
    rt_port="$("$(_python_bin)" -c "import json; print(json.load(open('${home}/runtime.json')).get('backend_port') or '')" 2>/dev/null || true)"
    [[ -n "$rt_port" ]] && port="$rt_port"
  fi
  curl -sf --max-time 2 "http://127.0.0.1:${port}/api/v1/health" >/dev/null 2>&1
}

if [[ "$DO_CHECK_UPDATE" == true ]]; then
  "$(_python_bin)" -m excelmanus.upgrade --check --project-root "$PROJECT_ROOT"
  exit $?
fi

if [[ "$DO_UPDATE" == true ]]; then
  if _api_is_running; then
    error "API 仍在运行。请在设置页执行更新，或先停止服务再使用 --update。"
    exit 1
  fi
  info "正在停机更新..."
  "$(_python_bin)" -m excelmanus.upgrade --offline -y --project-root "$PROJECT_ROOT" || exit $?
  info "更新完成，继续启动..."
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
  _clear_runtime
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
  export EXCELMANUS_WEB_WORKERS="${WORKERS}"
  if [[ "${WORKERS}" -gt 1 ]]; then
    warn "检测到 ${WORKERS} 个 uvicorn worker。会话引擎是进程内存态，同一 session_id 落到不同 worker 会从 SQLite 重建信封；MCP 未连上或技能快照丢失时 tools/system 前缀不等值，将静默打满 prompt cache miss。单机请保持 workers=1；多实例扩容请在反代层按 session_id 粘性路由。"
  fi

  local backend_cmd=(.venv/bin/python -c 'from excelmanus.api import main; main()'
    --host "$BACKEND_HOST" --port "$BACKEND_PORT" --workers "$WORKERS")
  if [[ -n "$LOG_DIR" ]]; then
    "${backend_cmd[@]}" >> "${LOG_DIR}/backend.log" 2>&1 &
  else
    "${backend_cmd[@]}" &
  fi
  BACKEND_PID=$!
  debug "后端进程已启动 (PID $BACKEND_PID)"
}

_wait_backend() {
  local ready=false
  local tries=$((HEALTH_TIMEOUT * 5))
  local i
  for i in $(seq 1 "$tries"); do
    if curl -sf --max-time 1 "http://localhost:${BACKEND_PORT}/api/v1/health" >/dev/null 2>&1; then
      log "后端已就绪 (PID $BACKEND_PID)"
      ready=true
      break
    fi
    if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
      error "后端启动失败，请检查日志或到 Web 设置页完成模型配置"
      [[ -n "$LOG_DIR" ]] && error "查看日志: ${LOG_DIR}/backend.log"
      exit 1
    fi
    sleep 0.2
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

  # 将后端端口传递给 Next.js rewrite 代理（next.config.ts 读取 BACKEND_INTERNAL_URL）
  export BACKEND_INTERNAL_URL="http://127.0.0.1:${BACKEND_PORT}"

  if [[ -n "$LOG_DIR" ]]; then
    (cd web && HOSTNAME=127.0.0.1 PORT=${FRONTEND_PORT} exec $run_cmd >> "${LOG_DIR}/frontend.log" 2>&1) &
  else
    (cd web && HOSTNAME=127.0.0.1 PORT=${FRONTEND_PORT} exec $run_cmd) &
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

if [[ "$FRONTEND_ONLY" != true ]]; then
  _wait_backend
fi

# 等待前端启动
if [[ "$BACKEND_ONLY" != true ]]; then
  fe_ready=false
  for i in $(seq 1 60); do
    if curl -sf --max-time 1 "http://localhost:${FRONTEND_PORT}" >/dev/null 2>&1; then
      fe_ready=true
      break
    fi
    sleep 0.5
  done
  if [[ "$fe_ready" != true ]]; then
    echo -e "${YELLOW}前端在 30 秒内未就绪，请检查日志${NC}"
  fi
fi

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

_write_runtime

wait
