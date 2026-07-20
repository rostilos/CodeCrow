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

echo "--- 2. Validating and synchronizing service configuration ---"
REQUIRED_CONFIGS=(
    "$DOCKER_PATH/.env"
    "$CONFIG_PATH/java-shared/application.properties"
    "$CONFIG_PATH/java-shared/github-private-key/github-app-private-key.pem"
    "$CONFIG_PATH/inference-orchestrator/.env"
    "$CONFIG_PATH/rag-pipeline/.env"
    "$CONFIG_PATH/web-frontend/.env"
)
for CONFIG_FILE in "${REQUIRED_CONFIGS[@]}"; do
    if [ ! -f "$CONFIG_FILE" ] || [ ! -s "$CONFIG_FILE" ] || [ ! -r "$CONFIG_FILE" ]; then
        echo "ERROR: Configuration must exist, be non-empty, and be readable: $CONFIG_FILE" >&2
        exit 1
    fi
done

cp "$CONFIG_PATH/inference-orchestrator/.env" \
    "python-ecosystem/inference-orchestrator/src/.env"
cp "$CONFIG_PATH/rag-pipeline/.env" \
    "python-ecosystem/rag-pipeline/.env"
cp "$CONFIG_PATH/web-frontend/.env" "$FRONTEND_DIR/.env"

FRONTEND_ENV="$CONFIG_PATH/web-frontend/.env"
PUBLIC_WEB_FRONTEND_ENV_SHA256=$(sha256sum "$FRONTEND_ENV" | cut -d' ' -f1)
export PUBLIC_WEB_FRONTEND_ENV_SHA256
(cd "$DOCKER_PATH" && docker compose --env-file .env config --quiet)
echo "Service configuration synchronized and Compose configuration validated."

echo "--- 3. Building Java Artifacts (mvn clean package) ---"
(cd "$JAVA_DIR" && mvn clean package)

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
COMPOSE=(docker compose --env-file .env)
"${COMPOSE[@]}" down --remove-orphans

echo "--- 6. Building Docker images and starting services ---"
"${COMPOSE[@]}" up -d --build --wait

echo "--- Deployment Complete! Services are up and healthy. ---"
"${COMPOSE[@]}" ps
