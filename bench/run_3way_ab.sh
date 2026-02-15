#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────
# 三模式 AB 对比测试：OFF / ENRICHED / ANCHORED
# 用法：bash bench/run_3way_ab.sh [--suites suite1.json,suite2.json]
# 默认运行三个窗口感知相关套件
# ──────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# 优先使用项目 venv 中的 Python
if [[ -x "${PROJECT_ROOT}/.venv/bin/python" ]]; then
    PYTHON="${PROJECT_ROOT}/.venv/bin/python"
elif command -v python &>/dev/null; then
    PYTHON=python
elif command -v python3 &>/dev/null; then
    PYTHON=python3
else
    echo "❌ 未找到 python 或 python3"
    exit 1
fi
echo "使用 Python: $PYTHON"

# 默认套件
DEFAULT_SUITES=(
    "bench/cases/suite_window_perception_ab.json"
    "bench/cases/suite_window_perception_complex.json"
    "bench/cases/suite_15_多轮对话.json"
)

# 解析参数
SUITES=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --suites)
            IFS=',' read -ra SUITES <<< "$2"
            shift 2
            ;;
        *)
            echo "未知参数: $1"
            exit 1
            ;;
    esac
done

if [[ ${#SUITES[@]} -eq 0 ]]; then
    SUITES=("${DEFAULT_SUITES[@]}")
fi

# 时间戳目录
TS=$(date -u +"%Y%m%dT%H%M%S")
BASE_OUTPUT="outputs/bench_3way_${TS}"

echo "═══════════════════════════════════════════════════"
echo "  三模式 AB 对比测试"
echo "  输出目录: ${BASE_OUTPUT}"
echo "  套件数量: ${#SUITES[@]}"
echo "═══════════════════════════════════════════════════"

# 构建 --suite 参数
SUITE_ARGS=()
for s in "${SUITES[@]}"; do
    SUITE_ARGS+=("--suite" "$s")
done

# ── 模式 1: OFF ──
echo ""
echo "▶ [1/3] 模式 OFF — 窗口感知关闭"
echo "─────────────────────────────────"
EXCELMANUS_WINDOW_PERCEPTION_ENABLED=0 \
    $PYTHON -m excelmanus.bench \
    "${SUITE_ARGS[@]}" \
    --output-dir "${BASE_OUTPUT}/off" \
    --concurrency 1

echo "✓ OFF 模式完成"
echo ""

# 间隔 5 秒，避免 API 限流
sleep 5

# ── 模式 2: ENRICHED ──
echo "▶ [2/3] 模式 ENRICHED — 现有增强返回路径"
echo "──────────────────────────────────────────────"
EXCELMANUS_WINDOW_PERCEPTION_ENABLED=1 \
EXCELMANUS_WINDOW_RETURN_MODE=enriched \
    $PYTHON -m excelmanus.bench \
    "${SUITE_ARGS[@]}" \
    --output-dir "${BASE_OUTPUT}/enriched" \
    --concurrency 1

echo "✓ ENRICHED 模式完成"
echo ""

sleep 5

# ── 模式 3: ANCHORED ──
echo "▶ [3/3] 模式 ANCHORED — WURM Phase1 轻量确认"
echo "──────────────────────────────────────────────"
EXCELMANUS_WINDOW_PERCEPTION_ENABLED=1 \
EXCELMANUS_WINDOW_RETURN_MODE=anchored \
    $PYTHON -m excelmanus.bench \
    "${SUITE_ARGS[@]}" \
    --output-dir "${BASE_OUTPUT}/anchored" \
    --concurrency 1

echo "✓ ANCHORED 模式完成"
echo ""

echo "═══════════════════════════════════════════════════"
echo "  全部完成！输出目录: ${BASE_OUTPUT}"
echo "═══════════════════════════════════════════════════"
echo ""
echo "运行对比分析："
echo "  $PYTHON bench/analyze_3way.py ${BASE_OUTPUT}"
