#!/usr/bin/env bash
set -euo pipefail

if ! command -v rg >/dev/null 2>&1; then
  echo "[secret-scan] 未找到 rg (ripgrep)，无法执行扫描。" >&2
  exit 2
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT_DIR}"

declare -a GLOBS=(
  "mcp.json"
  ".env"
  ".env.*"
  "*.toml"
)

declare -a PATTERNS=(
  "ctx7sk-[A-Za-z0-9-]{8,}"
  "\\bsk-[A-Za-z0-9]{16,}\\b"
  "(?i)(api[-_]?key|token|secret|password)\\s*[:=]\\s*[\"'](?!\\$|\\$\\{)[^\"']{8,}[\"']"
  "(?i)--(api[-_]?key|token|secret|password)(?:=|\\s+)(?!\\$|\\$\\{)[A-Za-z0-9._-]{8,}"
)

found=0
declare -a TARGETS=()
if command -v git >/dev/null 2>&1; then
  while IFS= read -r file; do
    [[ -n "${file}" ]] && TARGETS+=("${file}")
  done < <(git ls-files -- "${GLOBS[@]}")
fi

if [[ ${#TARGETS[@]} -eq 0 ]]; then
  for glob in "${GLOBS[@]}"; do
    for path in ${glob}; do
      [[ -f "${path}" ]] && TARGETS+=("${path}")
    done
  done
fi

if [[ ${#TARGETS[@]} -eq 0 ]]; then
  echo "[secret-scan] 未发现待扫描文件，跳过。"
  exit 0
fi

for pattern in "${PATTERNS[@]}"; do
  if rg --pcre2 -n "${pattern}" "${TARGETS[@]}"; then
    found=1
  fi
done

if [[ ${found} -ne 0 ]]; then
  echo "[secret-scan] 检测到疑似明文凭证，请改为环境变量引用（\$VAR 或 \${VAR}）。" >&2
  exit 1
fi

echo "[secret-scan] 扫描通过。"
