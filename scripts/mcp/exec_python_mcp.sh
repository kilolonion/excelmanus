#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "用法: $0 <pip包规格> <可执行名> [参数...]" >&2
  exit 64
fi

PIP_SPEC="$1"
CMD_NAME="$2"
shift 2

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
STATE_ROOT="${EXCELMANUS_MCP_STATE_DIR:-${REPO_ROOT}/.excelmanus/mcp}"

SAFE_NAME="$(printf '%s' "${PIP_SPEC}" | tr '/:@=' '____')"
VENV_DIR="${STATE_ROOT}/py/${SAFE_NAME}"
BIN_PATH="${VENV_DIR}/bin/${CMD_NAME}"
SPEC_STAMP="${VENV_DIR}/.installed-spec"

needs_install="false"
if [[ ! -x "${BIN_PATH}" ]]; then
  needs_install="true"
fi
if [[ ! -f "${SPEC_STAMP}" ]]; then
  needs_install="true"
elif [[ "$(cat "${SPEC_STAMP}")" != "${PIP_SPEC}" ]]; then
  needs_install="true"
fi

if [[ "${needs_install}" == "true" ]]; then
  echo "[mcp-bootstrap] 安装 Python MCP 包: ${PIP_SPEC}" >&2
  mkdir -p "$(dirname "${VENV_DIR}")"
  if [[ ! -d "${VENV_DIR}" ]]; then
    python3 -m venv "${VENV_DIR}"
  fi
  "${VENV_DIR}/bin/pip" install --upgrade pip >/dev/null
  "${VENV_DIR}/bin/pip" install --upgrade "${PIP_SPEC}" >/dev/null
  printf '%s\n' "${PIP_SPEC}" > "${SPEC_STAMP}"
fi

exec "${BIN_PATH}" "$@"
