#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
target="${1:-all}"
keep_output="${2:-}"
extra_args=()

if [[ "$keep_output" == "keep" || "$keep_output" == "--no-clean-output" ]]; then
  extra_args+=(--no-clean-output)
elif [[ -n "$keep_output" ]]; then
  echo "Usage: bash scripts/package-release-macos.sh [all|desktop|android] [keep|--no-clean-output]" >&2
  exit 2
fi

case "$target" in
  all)
    args=(--platform macos)
    ;;
  desktop)
    args=(--platform macos --desktop-only)
    ;;
  android)
    args=(--platform android --android-only)
    ;;
  *)
    echo "Usage: bash scripts/package-release-macos.sh [all|desktop|android] [keep|--no-clean-output]" >&2
    exit 2
    ;;
esac

cd "$repo_root"
exec node scripts/package-release.mjs "${args[@]}" "${extra_args[@]}"
