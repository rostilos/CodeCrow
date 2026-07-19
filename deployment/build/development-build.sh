#!/bin/bash
set -e

MCP_SERVERS_JAR_PATH="java-ecosystem/mcp-servers/vcs-mcp/target/codecrow-vcs-mcp-1.0.jar"
PLATFORM_MCP_JAR_PATH="java-ecosystem/mcp-servers/platform-mcp/target/codecrow-platform-mcp-1.0.jar"
FRONTEND_DIR="frontend"
JAVA_DIR="java-ecosystem"
DOCKER_PATH="deployment"
CONFIG_PATH="deployment/config"

cd "$(dirname "$0")/../../"

echo "--- 1. Ensuring frontend submodule is synchronized ---"
git submodule update --init --recursive -- "$FRONTEND_DIR"
echo "Frontend pinned at: $(git -C "$FRONTEND_DIR" rev-parse --short HEAD)"

echo "--- 2. Preparing frontend build configuration ---"
FRONTEND_ENV="$CONFIG_PATH/web-frontend/.env"
if [ ! -f "$FRONTEND_ENV" ]; then
    echo "ERROR: Missing $FRONTEND_ENV" >&2
    exit 1
fi
PUBLIC_WEB_FRONTEND_ENV_SHA256=$(sha256sum "$FRONTEND_ENV" | cut -d' ' -f1)
export PUBLIC_WEB_FRONTEND_ENV_SHA256
echo "Frontend configuration will be mounted as a BuildKit secret."

echo "--- 3. Building Java Artifacts (mvn clean package) ---"
(cd "$JAVA_DIR" && mvn clean package -DskipTests)

echo "--- 4. MCP Servers jar update ---"
cp "$MCP_SERVERS_JAR_PATH" python-ecosystem/inference-orchestrator/src/codecrow-vcs-mcp-1.0.jar

echo "--- 4.1. Platform MCP jar update ---"
if [ -f "$PLATFORM_MCP_JAR_PATH" ]; then
    cp "$PLATFORM_MCP_JAR_PATH" python-ecosystem/inference-orchestrator/src/codecrow-platform-mcp-1.0.jar
    echo "Platform MCP JAR copied successfully."
else
    echo "Warning: Platform MCP JAR not found at $PLATFORM_MCP_JAR_PATH"
fi

echo "--- 5. Shutting down existing services cleanly ---"
cd "$DOCKER_PATH"
docker compose down --remove-orphans

echo "--- 6. Building Docker images and starting services ---"
docker compose up -d --build --wait

echo "--- Deployment Complete! Services are up and healthy. ---"
docker compose ps