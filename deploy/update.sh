#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
#  ExcelManus 一键更新脚本
#  覆盖旧版本，自动保留用户数据（.env / 数据库 / 用户文件）
#
#  用法:  ./deploy/update.sh [选项]
#
#  选项:
#    --check              仅检查是否有更新，不执行
#    --skip-backup        跳过数据备份（不推荐）
#    --skip-deps          跳过依赖重装
#    --mirror             使用国内镜像（清华 PyPI + npmmirror）
#    --force              强制覆盖（git reset --hard）
#    --rollback           从最近的备份恢复
#    --list-backups       列出所有备份
#    -y, --yes            跳过确认提示
#    -v, --verbose        详细输出
#    -h, --help           显示帮助
# ═══════════════════════════════════════════════════════════════════════

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ── 颜色 ──
if [[ -t 1 ]]; then
  G='\033[0;32m'; C='\033[0;36m'; R='\033[0;31m'
  Y='\033[0;33m'; B='\033[1m'; N='\033[0m'
else
  G=''; C=''; R=''; Y=''; B=''; N=''
fi

# ── 日志 ──
log()   { echo -e "${G}✅${N} $*"; }
info()  { echo -e "${C}ℹ️${N}  $*"; }
warn()  { echo -e "${Y}⚠️${N}  $*" >&2; }
error() { echo -e "${R}❌${N} $*" >&2; }
step()  { echo -e "\n${B}── $* ──${N}"; }

# ── 默认值 ──
CHECK_ONLY=false
SKIP_BACKUP=false
SKIP_DEPS=false
USE_MIRROR=false
FORCE=false
ROLLBACK=false
LIST_BACKUPS=false
AUTO_YES=false
VERBOSE=false

REPO_URL_GITEE="https://gitee.com/kilolonion/excelmanus.git"

# ── 网络探测：自动识别国内网络，优先使用镜像 ──
_detect_domestic_network() {
  # Single python3 call does parallel TCP pings internally
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
  [[ "$result" == "1" ]] && return 0 || return 1
}

# ── 检测 uv 包管理器 ──
_has_uv() {
  command -v uv &>/dev/null && return 0 || return 1
}

# ── 解析参数 ──
while [[ $# -gt 0 ]]; do
  case "$1" in
    --check)        CHECK_ONLY=true ;;
    --skip-backup)  SKIP_BACKUP=true ;;
    --skip-deps)    SKIP_DEPS=true ;;
    --mirror)       USE_MIRROR=true ;;
    --force)        FORCE=true ;;
    --rollback)     ROLLBACK=true ;;
    --list-backups) LIST_BACKUPS=true ;;
    -y|--yes)       AUTO_YES=true ;;
    -v|--verbose)   VERBOSE=true ;;
    -h|--help)
      sed -n '/^#  用法/,/^# ═/p' "${BASH_SOURCE[0]}" | sed 's/^# *//' | sed '$d'
      exit 0 ;;
    *) error "未知参数: $1"; exit 1 ;;
  esac
  shift
done

# ── 自动探测国内网络 ──
if [[ "$USE_MIRROR" != true ]]; then
  if _detect_domestic_network; then
    USE_MIRROR=true
    info "检测到国内网络，自动启用镜像加速"
  fi
fi

# ── 版本读取 ──
get_version() {
  grep -m1 'version' "${PROJECT_ROOT}/pyproject.toml" 2>/dev/null \
    | sed 's/.*=\s*"\(.*\)"/\1/' || echo "unknown"
}

CURRENT_VERSION="$(get_version)"

# ── 列出备份 ──
if [[ "$LIST_BACKUPS" == true ]]; then
  BACKUP_DIR="${PROJECT_ROOT}/backups"
  if [[ ! -d "$BACKUP_DIR" ]]; then
    info "暂无备份"
    exit 0
  fi
  echo -e "${B}可用备份:${N}"
  for d in $(ls -1dr "${BACKUP_DIR}"/backup_* 2>/dev/null); do
    name="$(basename "$d")"
    size="$(du -sh "$d" 2>/dev/null | cut -f1)"
    echo "  ${C}${name}${N}  (${size})"
  done
  exit 0
fi

# ── 回滚 ──
if [[ "$ROLLBACK" == true ]]; then
  BACKUP_DIR="${PROJECT_ROOT}/backups"
  LATEST="$(ls -1d "${BACKUP_DIR}"/backup_* 2>/dev/null | tail -1 || true)"
  if [[ -z "$LATEST" ]]; then
    error "未找到任何备份"
    exit 1
  fi
  step "从备份恢复: $(basename "$LATEST")"
  # 恢复 .env
  [[ -f "${LATEST}/.env" ]] && cp -f "${LATEST}/.env" "${PROJECT_ROOT}/.env" && log "恢复 .env"
  # 恢复目录
  for dir_name in users outputs uploads; do
    if [[ -d "${LATEST}/${dir_name}" ]]; then
      rm -rf "${PROJECT_ROOT}/${dir_name}"
      cp -a "${LATEST}/${dir_name}" "${PROJECT_ROOT}/${dir_name}"
      log "恢复 ${dir_name}/"
    fi
  done
  # 恢复数据库
  if [[ -d "${LATEST}/.excelmanus_home" ]]; then
    mkdir -p "${HOME}/.excelmanus"
    cp -f "${LATEST}/.excelmanus_home"/*.db* "${HOME}/.excelmanus/" 2>/dev/null && log "恢复数据库"
  fi
  log "回滚完成！请重启服务。"
  exit 0
fi

# ── 主流程 ──
echo ""
echo -e "${C}  ╔══════════════════════════════════════╗${N}"
echo -e "${C}  ║     ExcelManus 更新工具 v1.0        ║${N}"
echo -e "${C}  ╚══════════════════════════════════════╝${N}"
echo ""
info "当前版本: ${B}${CURRENT_VERSION}${N}"
info "项目目录: ${PROJECT_ROOT}"
echo ""

# ── 检查 Git ──
if [[ ! -d "${PROJECT_ROOT}/.git" ]]; then
  error "项目不是 Git 仓库，无法通过 git 更新"
  error "请手动从 ${REPO_URL:-https://github.com/kilolonion/excelmanus} 下载最新版本"
  exit 1
fi

if ! command -v git &>/dev/null; then
  error "未找到 Git，请先安装"
  exit 1
fi

# ── Step 1: 检查更新（国内自动 Gitee 加速） ──
step "检查更新"
if ! git -C "$PROJECT_ROOT" fetch origin --tags --quiet 2>/dev/null; then
  if [[ "$USE_MIRROR" == true ]]; then
    warn "GitHub fetch 失败，尝试 Gitee 镜像..."
    git -C "$PROJECT_ROOT" remote add gitee "$REPO_URL_GITEE" 2>/dev/null || true
    git -C "$PROJECT_ROOT" remote set-url gitee "$REPO_URL_GITEE" 2>/dev/null || true
    git -C "$PROJECT_ROOT" fetch gitee --tags --quiet 2>/dev/null || {
      warn "Gitee fetch 也失败，请检查网络连接"
      exit 1
    }
  else
    warn "git fetch 失败，请检查网络连接"
    exit 1
  fi
fi

BRANCH="$(git -C "$PROJECT_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "main")"
BEHIND="$(git -C "$PROJECT_ROOT" rev-list --count "HEAD..origin/${BRANCH}" 2>/dev/null || echo "0")"

if [[ "$BEHIND" -eq 0 ]]; then
  log "已是最新版本 (${CURRENT_VERSION})，无需更新"
  exit 0
fi

# 获取远端版本
REMOTE_VERSION="$(git -C "$PROJECT_ROOT" show "origin/${BRANCH}:pyproject.toml" 2>/dev/null \
  | grep -m1 'version' | sed 's/.*=\s*"\(.*\)"/\1/' || echo "unknown")"

echo ""
info "发现新版本!"
info "  当前: ${R}${CURRENT_VERSION}${N}"
info "  最新: ${G}${REMOTE_VERSION}${N}"
info "  落后: ${BEHIND} 个提交"
echo ""

if [[ "$VERBOSE" == true ]]; then
  info "最近更新内容:"
  git -C "$PROJECT_ROOT" log "HEAD..origin/${BRANCH}" --oneline -10 | sed 's/^/    /'
  echo ""
fi

if [[ "$CHECK_ONLY" == true ]]; then
  exit 0
fi

# ── 确认 ──
if [[ "$AUTO_YES" != true ]]; then
  echo -e -n "${Y}是否开始更新？[Y/n] ${N}"
  read -r confirm
  if [[ "$confirm" =~ ^[Nn] ]]; then
    info "已取消"
    exit 0
  fi
fi

# ── Step 2: 备份 ──
if [[ "$SKIP_BACKUP" != true ]]; then
  step "备份用户数据"
  BACKUP_TS="$(date +%Y%m%d_%H%M%S)"
  BACKUP_PATH="${PROJECT_ROOT}/backups/backup_${CURRENT_VERSION}_${BACKUP_TS}"
  mkdir -p "$BACKUP_PATH"

  for item in .env users outputs uploads; do
    src="${PROJECT_ROOT}/${item}"
    if [[ -f "$src" ]]; then
      cp -f "$src" "${BACKUP_PATH}/${item}"
      log "备份: ${item}"
    elif [[ -d "$src" ]] && [[ -n "$(ls -A "$src" 2>/dev/null)" ]]; then
      cp -a "$src" "${BACKUP_PATH}/${item}"
      log "备份: ${item}/"
    fi
  done

  # 备份外部数据库
  if [[ -d "${HOME}/.excelmanus" ]]; then
    mkdir -p "${BACKUP_PATH}/.excelmanus_home"
    cp -f "${HOME}/.excelmanus"/*.db* "${BACKUP_PATH}/.excelmanus_home/" 2>/dev/null && log "备份: 数据库"
  fi
  log "备份完成: ${BACKUP_PATH}"
else
  warn "跳过备份（--skip-backup）"
fi

# ── Step 3: 更新代码 ──
step "拉取最新代码"

# 暂存本地修改（如果有）
if [[ -n "$(git -C "$PROJECT_ROOT" status --porcelain 2>/dev/null)" ]]; then
  info "暂存本地修改..."
  git -C "$PROJECT_ROOT" stash --include-untracked --quiet 2>/dev/null || true
fi

if [[ "$FORCE" == true ]]; then
  info "强制覆盖模式 (git reset --hard)"
  git -C "$PROJECT_ROOT" reset --hard "origin/${BRANCH}" --quiet
else
  git -C "$PROJECT_ROOT" pull origin "$BRANCH" --ff-only --quiet 2>/dev/null || {
    warn "fast-forward 失败，执行强制覆盖..."
    git -C "$PROJECT_ROOT" reset --hard "origin/${BRANCH}" --quiet
  }
fi
NEW_VERSION="$(get_version)"
log "代码已更新: ${CURRENT_VERSION} → ${NEW_VERSION}"

# ── Step 4: 更新依赖（pip + npm 并行，uv 优先，镜像加速） ──
if [[ "$SKIP_DEPS" != true ]]; then
  step "并行安装依赖"

  # 查找 Python
  if [[ -f "${PROJECT_ROOT}/.venv/bin/python" ]]; then
    PY="${PROJECT_ROOT}/.venv/bin/python"
  elif [[ -f "${PROJECT_ROOT}/.venv/Scripts/python.exe" ]]; then
    PY="${PROJECT_ROOT}/.venv/Scripts/python.exe"
  else
    PY="python3"
  fi

  # 后端依赖（优先 uv，回退 pip）
  _install_backend() {
    local mirror_arg=""
    [[ "$USE_MIRROR" == true ]] && mirror_arg="-i https://pypi.tuna.tsinghua.edu.cn/simple"

    if _has_uv; then
      info "使用 uv 安装后端依赖（加速模式）..."
      uv pip install -e "${PROJECT_ROOT}" $mirror_arg --quiet 2>/dev/null && return 0
    fi
    "$PY" -m pip install -e "${PROJECT_ROOT}" $mirror_arg --quiet 2>/dev/null && return 0
    # 回退: 强制使用镜像
    if [[ "$USE_MIRROR" != true ]]; then
      warn "pip install 失败，尝试清华镜像..."
      "$PY" -m pip install -e "${PROJECT_ROOT}" -i "https://pypi.tuna.tsinghua.edu.cn/simple" --quiet 2>/dev/null && return 0
    fi
    return 1
  }

  # 前端依赖
  _install_frontend() {
    local web_dir="${PROJECT_ROOT}/web"
    [[ -d "$web_dir" ]] && [[ -f "${web_dir}/package.json" ]] || return 0
    local npm_args=(install --prefix "$web_dir" --silent)
    [[ "$USE_MIRROR" == true ]] && npm_args+=(--registry=https://registry.npmmirror.com)
    npm "${npm_args[@]}" 2>/dev/null && return 0
    # 回退: 强制使用 npmmirror
    if [[ "$USE_MIRROR" != true ]]; then
      warn "npm install 失败，尝试 npmmirror..."
      npm install --registry=https://registry.npmmirror.com --prefix "$web_dir" --silent 2>/dev/null && return 0
    fi
    return 1
  }

  # 并行执行 pip + npm
  BE_OK_FILE=$(mktemp)
  FE_OK_FILE=$(mktemp)
  trap "rm -f '$BE_OK_FILE' '$FE_OK_FILE'" EXIT

  ( _install_backend && echo "1" > "$BE_OK_FILE" || echo "0" > "$BE_OK_FILE" ) &
  PID_BE=$!
  ( _install_frontend && echo "1" > "$FE_OK_FILE" || echo "0" > "$FE_OK_FILE" ) &
  PID_FE=$!

  info "后端 + 前端依赖并行安装中..."
  wait $PID_BE 2>/dev/null
  wait $PID_FE 2>/dev/null

  if [[ "$(cat "$BE_OK_FILE" 2>/dev/null)" == "1" ]]; then
    log "后端依赖已更新"
  else
    error "后端依赖安装失败"
    warn "你可以手动运行: ${PY} -m pip install -e ."
  fi

  if [[ "$(cat "$FE_OK_FILE" 2>/dev/null)" == "1" ]]; then
    log "前端依赖已更新"
  else
    warn "前端依赖安装失败（非致命），你可以手动运行: cd web && npm install"
  fi

  rm -f "$BE_OK_FILE" "$FE_OK_FILE"
else
  warn "跳过依赖更新（--skip-deps）"
fi

# ── 完成 ──
echo ""
echo -e "${G}  ╔══════════════════════════════════════╗${N}"
echo -e "${G}  ║         更新成功！                   ║${N}"
echo -e "${G}  ╚══════════════════════════════════════╝${N}"
echo ""
info "版本: ${R}${CURRENT_VERSION}${N} → ${G}${NEW_VERSION}${N}"
info "数据库迁移将在下次启动时自动执行"
echo ""
info "请重启服务以应用更新:"
info "  ./deploy/start.sh"
echo ""
