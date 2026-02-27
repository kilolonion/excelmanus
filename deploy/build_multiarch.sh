#!/usr/bin/env bash
# ============================================================================
# ExcelManus 多平台 Docker 镜像构建脚本
# 支持平台: linux/amd64, linux/arm64, linux/arm/v7
#
# 用法:
#   ./deploy/build_multiarch.sh              # 仅构建（不推送）
#   ./deploy/build_multiarch.sh --push       # 构建并推送到 Docker Hub
#   ./deploy/build_multiarch.sh --load       # 构建并加载到本地（仅限单平台）
#
# 环境变量:
#   REGISTRY    - 镜像仓库前缀，如 "docker.io/myuser" 或 "ghcr.io/myorg"
#   VERSION     - 镜像版本标签，默认从 pyproject.toml 读取
#   PLATFORMS   - 目标平台列表，默认 "linux/amd64,linux/arm64,linux/arm/v7"
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ---------- 配置 ----------
REGISTRY="${REGISTRY:-excelmanus}"
VERSION="${VERSION:-$(grep -oP 'version\s*=\s*"\K[^"]+' "$PROJECT_ROOT/pyproject.toml")}"
PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64,linux/arm/v7}"
BUILDER_NAME="excelmanus-multiarch"

# ---------- 参数解析 ----------
ACTION=""
for arg in "$@"; do
  case "$arg" in
    --push) ACTION="--push" ;;
    --load) ACTION="--load" ;;
    *) echo "未知参数: $arg"; exit 1 ;;
  esac
done

# --load 仅支持单平台
if [ "$ACTION" = "--load" ]; then
  echo "⚠️  --load 模式仅支持单平台，将只构建当前架构"
  PLATFORMS=""
fi

echo "============================================"
echo " ExcelManus 多平台 Docker 镜像构建"
echo "============================================"
echo " 仓库前缀:  $REGISTRY"
echo " 版本:      $VERSION"
echo " 目标平台:  ${PLATFORMS:-当前架构}"
echo " 操作:      ${ACTION:-仅构建(不推送)}"
echo "============================================"
echo ""

# ---------- 确保 buildx builder 存在 ----------
setup_builder() {
  if ! docker buildx inspect "$BUILDER_NAME" &>/dev/null; then
    echo "🔧 创建 buildx builder: $BUILDER_NAME"
    docker buildx create --name "$BUILDER_NAME" --driver docker-container --use
  else
    docker buildx use "$BUILDER_NAME"
  fi
  # 启动 builder 并确保 QEMU 模拟器已注册
  docker buildx inspect --bootstrap
}

# ---------- 构建函数 ----------
build_image() {
  local image_name="$1"
  local dockerfile="$2"
  local context="$3"
  shift 3
  local extra_args=("$@")

  local full_tag="${REGISTRY}/${image_name}:${VERSION}"
  local latest_tag="${REGISTRY}/${image_name}:latest"

  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "📦 构建镜像: $full_tag"
  echo "   Dockerfile: $dockerfile"
  echo "   Context:    $context"
  echo "   平台:       ${PLATFORMS:-当前架构}"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

  local cmd=(
    docker buildx build
    -f "$dockerfile"
    -t "$full_tag"
    -t "$latest_tag"
  )

  if [ -n "$PLATFORMS" ]; then
    cmd+=(--platform "$PLATFORMS")
  fi

  if [ -n "$ACTION" ]; then
    cmd+=("$ACTION")
  fi

  cmd+=("${extra_args[@]}")
  cmd+=("$context")

  "${cmd[@]}"

  echo "✅ 完成: $full_tag"
}

# ---------- 主流程 ----------
main() {
  setup_builder

  echo ""
  echo "🚀 开始构建三个镜像..."
  echo ""

  # 1. Backend API
  build_image "excelmanus-api" \
    "$PROJECT_ROOT/deploy/Dockerfile" \
    "$PROJECT_ROOT"

  # 2. Sandbox
  build_image "excelmanus-sandbox" \
    "$PROJECT_ROOT/deploy/Dockerfile.sandbox" \
    "$PROJECT_ROOT"

  # 3. Frontend Web
  build_image "excelmanus-web" \
    "$PROJECT_ROOT/web/Dockerfile" \
    "$PROJECT_ROOT/web"

  echo ""
  echo "============================================"
  echo "🎉 全部镜像构建完成！"
  echo "============================================"
  echo ""
  echo "镜像列表:"
  echo "  - ${REGISTRY}/excelmanus-api:${VERSION}"
  echo "  - ${REGISTRY}/excelmanus-sandbox:${VERSION}"
  echo "  - ${REGISTRY}/excelmanus-web:${VERSION}"
  echo ""

  if [ -z "$ACTION" ]; then
    echo "💡 提示: 镜像已构建在 buildx 缓存中。"
    echo "   推送到仓库:  $0 --push"
    echo "   加载到本地:  $0 --load  (仅单平台)"
  fi
}

main
