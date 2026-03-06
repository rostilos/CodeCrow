#!/bin/bash
###############################################################################
# ci-build.sh — Runs inside the GitHub Actions runner.
#
# 1. Writes .env files from GitHub secrets
# 2. Builds & tests Java artifacts (Maven)  — fails fast on test errors
# 3. Copies MCP JARs to inference-orchestrator context
# 4. Builds all 5 Docker images and pushes them to GHCR
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
# GITHUB_REPOSITORY_OWNER is assumed to be provided for ghcr.io paths
REPO_OWNER=${GITHUB_REPOSITORY_OWNER:-codecrow}
REPO_OWNER=$(echo "$REPO_OWNER" | tr '[:upper:]' '[:lower:]')

cd "$ROOT_DIR"

echo "=========================================="
echo "  CodeCrow CI Build"
echo "=========================================="

# ── 1. Inject .env files from secrets ──────────────────────────────────────
echo "--- 1. Writing .env files from CI secrets ---"

if [ -n "${ENV_INFERENCE_ORCHESTRATOR:-}" ]; then
  echo "$ENV_INFERENCE_ORCHESTRATOR" > python-ecosystem/inference-orchestrator/src/.env
  echo "  ✓ inference-orchestrator/src/.env written"
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
(cd "$JAVA_DIR" && mvn clean verify -T 1C)
echo "  ✓ Java build & tests complete"

# ── 3. Copy MCP JARs ──────────────────────────────────────────────────────
echo "--- 3. Copying MCP server JARs ---"
cp "$MCP_JAR" python-ecosystem/inference-orchestrator/src/codecrow-vcs-mcp-1.0.jar
echo "  ✓ VCS MCP JAR copied"

if [ -f "$PLATFORM_MCP_JAR" ]; then
  cp "$PLATFORM_MCP_JAR" python-ecosystem/inference-orchestrator/src/codecrow-platform-mcp-1.0.jar
  echo "  ✓ Platform MCP JAR copied"
else
  echo "  ⚠ Platform MCP JAR not found (optional)"
fi

# ── 4. Build Docker Images (with GitHub Actions layer cache) ───────────────
echo "--- 4. Building Docker images ---"

IMAGES=(
  "codecrow/web-server|java-ecosystem/services/web-server|Dockerfile.observable"
  "codecrow/pipeline-agent|java-ecosystem/services/pipeline-agent|Dockerfile.observable"
  "codecrow/inference-orchestrator|python-ecosystem/inference-orchestrator/src|Dockerfile.observable"
  "codecrow/rag-pipeline|python-ecosystem/rag-pipeline"
  "codecrow/web-frontend|frontend"
)

# Use BuildKit + GitHub Actions cache backend so base-image pulls, pip install,
# npm ci, apk add, etc. are cached across CI runs.

for entry in "${IMAGES[@]}"; do
  IFS='|' read -r IMAGE_NAME CONTEXT DOCKERFILE <<< "$entry"
  # Scope cache per image to avoid collisions
  SCOPE="$(echo "$IMAGE_NAME" | tr '/' '-')"
  
  # Map codecrow to ghcr.io/<repo-owner>/codecrow-<service>
  # E.g. codecrow/web-server -> ghcr.io/username/codecrow-web-server:latest
  SERVICE_NAME=$(echo "$IMAGE_NAME" | cut -d'/' -f2)
  FULL_IMAGE_NAME="ghcr.io/$REPO_OWNER/codecrow-$SERVICE_NAME:latest"

  echo "  Building and pushing $FULL_IMAGE_NAME from $CONTEXT ..."
  if [ -n "${DOCKERFILE:-}" ]; then
    docker buildx build \
      --cache-from "type=gha,scope=$SCOPE" \
      --cache-to "type=gha,mode=max,scope=$SCOPE" \
      --push \
      -t "$FULL_IMAGE_NAME" \
      -f "$CONTEXT/$DOCKERFILE" \
      "$CONTEXT"
  else
    docker buildx build \
      --cache-from "type=gha,scope=$SCOPE" \
      --cache-to "type=gha,mode=max,scope=$SCOPE" \
      --push \
      -t "$FULL_IMAGE_NAME" \
      "$CONTEXT"
  fi
  echo "  ✓ $FULL_IMAGE_NAME built and pushed"
done

echo ""
echo "=========================================="
echo "  Build and push complete!"
echo "=========================================="
