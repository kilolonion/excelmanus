#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
#  ExcelManus 一键更新脚本
#  包装 python -m excelmanus.upgrade（停机更新，禁止冲突时 reset --hard）
#
#  用法:  ./deploy/update.sh [选项]
#
#  选项:
#    --check              仅检查是否有更新，不执行
#    --skip-backup        跳过数据备份（不推荐）
#    --skip-deps          跳过依赖重装
#    --mirror             使用国内镜像（清华 PyPI + npmmirror）
#    --rollback           从最近的备份恢复（服务必须已停止）
#    --list-backups       列出所有备份
#    -y, --yes            跳过确认提示
#    -h, --help           显示帮助
# ═══════════════════════════════════════════════════════════════════════

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_ROOT"

if [[ -x "${PROJECT_ROOT}/.venv/bin/python" ]]; then
  PY="${PROJECT_ROOT}/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PY="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  PY="$(command -v python)"
else
  echo "未找到 Python。请先创建 .venv，或安装 Python 3.11+。" >&2
  exit 1
fi

ARGS=()
ROLLBACK=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --check)        ARGS+=(--check) ;;
    --skip-backup)  ARGS+=(--skip-backup) ;;
    --skip-deps)    ARGS+=(--skip-deps) ;;
    --mirror)       ARGS+=(--mirror) ;;
    --force)
      echo "已取消 --force：不再支持 git reset --hard 覆盖冲突。请手动解决后再更新。" >&2
      exit 1 ;;
    --rollback)     ROLLBACK=true ;;
    --list-backups) ARGS+=(--list-backups) ;;
    -y|--yes)       ARGS+=(-y) ;;
    -v|--verbose)   ;;
    -h|--help)
      sed -n '/^#  用法/,/^# ═/p' "${BASH_SOURCE[0]}" | sed 's/^# *//' | sed '$d'
      exit 0 ;;
    *) echo "未知参数: $1" >&2; exit 1 ;;
  esac
  shift
done

if [[ "$ROLLBACK" == true ]]; then
  latest="$("$PY" -m excelmanus.upgrade --list-backups --project-root "$PROJECT_ROOT" 2>/dev/null \
    | awk 'NF && $1 != "暂无备份" {print $1; exit}')"
  if [[ -z "$latest" || "$latest" == "暂无备份" ]]; then
    echo "未找到任何备份" >&2
    exit 1
  fi
  exec "$PY" -m excelmanus.upgrade --restore "$latest" --project-root "$PROJECT_ROOT"
fi

if [[ ${#ARGS[@]} -gt 0 ]]; then
  for a in "${ARGS[@]}"; do
    case "$a" in
      --check|--list-backups)
        exec "$PY" -m excelmanus.upgrade --project-root "$PROJECT_ROOT" "${ARGS[@]}"
        ;;
    esac
  done
fi

exec "$PY" -m excelmanus.upgrade --offline --project-root "$PROJECT_ROOT" "${ARGS[@]}"
