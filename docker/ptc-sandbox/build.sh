#!/bin/bash
#
# Build PTC Sandbox Docker Images
#
# Usage:
#   ./build.sh              # Build default datascience image
#   ./build.sh minimal      # Build minimal image
#   ./build.sh all          # Build all variants
#   ./build.sh --push       # Build and push to registry
#
# Examples:
#   ./build.sh                           # Build ptc-sandbox:datascience
#   ./build.sh minimal                   # Build ptc-sandbox:minimal
#   ./build.sh all                       # Build all images
#   ./build.sh --registry myregistry.com # Build with custom registry prefix

set -e

# Default values
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_NAME="ptc-sandbox"
REGISTRY=""
PUSH=false
BUILD_TARGET="datascience"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        minimal)
            BUILD_TARGET="minimal"
            shift
            ;;
        all)
            BUILD_TARGET="all"
            shift
            ;;
        --push)
            PUSH=true
            shift
            ;;
        --registry)
            REGISTRY="$2/"
            shift 2
            ;;
        --help|-h)
            echo "Usage: $0 [minimal|all] [--push] [--registry REGISTRY]"
            echo ""
            echo "Options:"
            echo "  minimal           Build minimal image only"
            echo "  all               Build all image variants"
            echo "  --push            Push images to registry after build"
            echo "  --registry REG    Use custom registry prefix (e.g., myregistry.com)"
            echo ""
            echo "Images:"
            echo "  ptc-sandbox:datascience  Full data science stack (~800MB)"
            echo "  ptc-sandbox:minimal      Minimal packages (~200MB)"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

cd "$SCRIPT_DIR"

# Build function
build_image() {
    local tag=$1
    local dockerfile=$2
    local full_tag="${REGISTRY}${IMAGE_NAME}:${tag}"

    echo "=========================================="
    echo "Building: $full_tag"
    echo "Dockerfile: $dockerfile"
    echo "=========================================="

    docker build -t "$full_tag" -f "$dockerfile" .

    if [ "$PUSH" = true ]; then
        echo "Pushing: $full_tag"
        docker push "$full_tag"
    fi

    echo ""
    echo "Successfully built: $full_tag"
    echo "Image size: $(docker images "$full_tag" --format '{{.Size}}')"
    echo ""
}

# Build based on target
case $BUILD_TARGET in
    datascience)
        build_image "datascience" "Dockerfile"
        build_image "latest" "Dockerfile"
        ;;
    minimal)
        build_image "minimal" "Dockerfile.minimal"
        ;;
    all)
        build_image "datascience" "Dockerfile"
        build_image "minimal" "Dockerfile.minimal"
        build_image "latest" "Dockerfile"
        ;;
esac

echo "=========================================="
echo "Build complete!"
echo ""
echo "To use the custom image, set in your .env:"
echo "  PTC_SANDBOX_IMAGE=${REGISTRY}${IMAGE_NAME}:${BUILD_TARGET}"
echo ""
echo "Or export the environment variable:"
echo "  export PTC_SANDBOX_IMAGE=${REGISTRY}${IMAGE_NAME}:${BUILD_TARGET}"
echo "=========================================="
