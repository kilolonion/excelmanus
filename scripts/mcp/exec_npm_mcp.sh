#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "用法: $0 <npm包规格> <二进制名> [参数...]" >&2
  exit 64
fi

PACKAGE_SPEC="$1"
BIN_NAME="$2"
shift 2

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
STATE_ROOT="${EXCELMANUS_MCP_STATE_DIR:-${REPO_ROOT}/.excelmanus/mcp}"
NPM_CACHE_DIR="${STATE_ROOT}/npm-cache"

SAFE_NAME="$(printf '%s' "${PACKAGE_SPEC}" | tr '/:@' '___')"
PREFIX_DIR="${STATE_ROOT}/npm/${SAFE_NAME}"
BIN_PATH="${PREFIX_DIR}/node_modules/.bin/${BIN_NAME}"
SPEC_STAMP="${PREFIX_DIR}/.installed-spec"

needs_install="false"
if [[ ! -x "${BIN_PATH}" ]]; then
  needs_install="true"
fi
if [[ ! -f "${SPEC_STAMP}" ]]; then
  needs_install="true"
elif [[ "$(cat "${SPEC_STAMP}")" != "${PACKAGE_SPEC}" ]]; then
  needs_install="true"
fi

if [[ "${needs_install}" == "true" ]]; then
  echo "[mcp-bootstrap] 安装 npm MCP 包: ${PACKAGE_SPEC}" >&2
  mkdir -p "${PREFIX_DIR}" "${NPM_CACHE_DIR}"
  npm install \
    --prefix "${PREFIX_DIR}" \
    --no-audit \
    --no-fund \
    --prefer-offline \
    "${PACKAGE_SPEC}" >/dev/null
  printf '%s\n' "${PACKAGE_SPEC}" > "${SPEC_STAMP}"
fi

exec "${BIN_PATH}" "$@"
