#!/bin/bash
###############################################################################
# ci-build.sh — Runs inside the GitHub Actions runner.
#
# 1. Writes .env files from GitHub secrets
# 2. Builds & tests Java artifacts (Maven)  — fails fast on test errors
# 3. Copies MCP JARs to inference-orchestrator context
# 4. Builds all 5 Docker images
# 5. Saves them to a single compressed tarball
#
# Required env vars (set by GH Actions):
#   ENV_INFERENCE_ORCHESTRATOR  — contents of inference-orchestrator/.env
#   ENV_RAG_PIPELINE            — contents of rag-pipeline/.env
#   ENV_WEB_FRONTEND            — contents of web-frontend/.env
###############################################################################
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

MCP_JAR="java-ecosystem/mcp-servers/vcs-mcp/target/codecrow-vcs-mcp-1.0.jar"
PLATFORM_MCP_JAR="java-ecosystem/mcp-servers/platform-mcp/target/codecrow-platform-mcp-1.0.jar"
JAVA_DIR="java-ecosystem"
OUTPUT_DIR="$ROOT_DIR/build-output"

cd "$ROOT_DIR"
mkdir -p "$OUTPUT_DIR"

echo "=========================================="
echo "  CodeCrow CI Build"
echo "=========================================="

# ── 1. Inject .env files from secrets ──────────────────────────────────────
echo "--- 1. Writing .env files from CI secrets ---"

if [ -n "${ENV_INFERENCE_ORCHESTRATOR:-}" ]; then
  echo "$ENV_INFERENCE_ORCHESTRATOR" > python-ecosystem/inference-orchestrator/.env
  echo "  ✓ inference-orchestrator/.env written"
fi

if [ -n "${ENV_RAG_PIPELINE:-}" ]; then
  echo "$ENV_RAG_PIPELINE" > python-ecosystem/rag-pipeline/.env
  echo "  ✓ rag-pipeline/.env written"
fi

if [ -n "${ENV_WEB_FRONTEND:-}" ]; then
  echo "$ENV_WEB_FRONTEND" > frontend/.env
  echo "  ✓ web-frontend/.env written"
fi

# ── 2. Build & Test Java Artifacts ─────────────────────────────────────────
echo "--- 2. Building & testing Java artifacts (mvn clean verify) ---"
(cd "$JAVA_DIR" && mvn clean verify)
echo "  ✓ Java build & tests complete"

# ── 3. Copy MCP JARs ──────────────────────────────────────────────────────
echo "--- 3. Copying MCP server JARs ---"
cp "$MCP_JAR" python-ecosystem/inference-orchestrator/codecrow-vcs-mcp-1.0.jar
echo "  ✓ VCS MCP JAR copied"

if [ -f "$PLATFORM_MCP_JAR" ]; then
  cp "$PLATFORM_MCP_JAR" python-ecosystem/inference-orchestrator/codecrow-platform-mcp-1.0.jar
  echo "  ✓ Platform MCP JAR copied"
else
  echo "  ⚠ Platform MCP JAR not found (optional)"
fi

# ── 4. Build Docker Images ────────────────────────────────────────────────
echo "--- 4. Building Docker images ---"

IMAGES=(
  "codecrow/web-server|java-ecosystem/services/web-server|Dockerfile.observable"
  "codecrow/pipeline-agent|java-ecosystem/services/pipeline-agent|Dockerfile.observable"
  "codecrow/inference-orchestrator|python-ecosystem/inference-orchestrator"
  "codecrow/rag-pipeline|python-ecosystem/rag-pipeline"
  "codecrow/web-frontend|frontend"
)

for entry in "${IMAGES[@]}"; do
  IFS='|' read -r IMAGE_NAME CONTEXT DOCKERFILE <<< "$entry"
  echo "  Building $IMAGE_NAME from $CONTEXT ..."
  if [ -n "${DOCKERFILE:-}" ]; then
    docker build -t "${IMAGE_NAME}:latest" -f "$CONTEXT/$DOCKERFILE" "$CONTEXT"
  else
    docker build -t "${IMAGE_NAME}:latest" "$CONTEXT"
  fi
  echo "  ✓ $IMAGE_NAME built"
done

# ── 5. Save images to tarball ─────────────────────────────────────────────
echo "--- 5. Saving Docker images to tarball ---"

IMAGE_LIST=""
for entry in "${IMAGES[@]}"; do
  IFS='|' read -r IMAGE_NAME _ _ <<< "$entry"
  IMAGE_LIST="$IMAGE_LIST ${IMAGE_NAME}:latest"
done

docker save $IMAGE_LIST | zstd -T0 --ultra -20 -o "$OUTPUT_DIR/codecrow-images.tar.zst"

TARBALL_SIZE=$(du -h "$OUTPUT_DIR/codecrow-images.tar.zst" | cut -f1)
echo "  ✓ Tarball created: codecrow-images.tar.zst ($TARBALL_SIZE)"

echo ""
echo "=========================================="
echo "  Build complete! Artifact: build-output/codecrow-images.tar.zst"
echo "=========================================="
