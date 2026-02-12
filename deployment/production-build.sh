#!/bin/bash
set -e

MCP_SERVERS_JAR_PATH="java-ecosystem/mcp-servers/vcs-mcp/target/codecrow-vcs-mcp-1.0.jar"
PLATFORM_MCP_JAR_PATH="java-ecosystem/mcp-servers/platform-mcp/target/codecrow-platform-mcp-1.0.jar"
FRONTEND_REPO_URL="git@github.com:rostilos/CodeCrow-Frontend.git"
FRONTEND_DIR="frontend"
JAVA_DIR="java-ecosystem"
DOCKER_PATH="deployment"
CONFIG_PATH="deployment/config"

cd "$(dirname "$0")/../"

echo "--- 1. Ensuring frontend code is synchronized ---"

if [ -d "$FRONTEND_DIR" ]; then
   echo "Frontend directory exists. Syncing local repository"
   (cd "$FRONTEND_DIR" && git fetch --all && git reset --hard origin/epic/CA-1-self-host)
else
   echo "Cloning frontend repository..."
   git clone "$FRONTEND_REPO_URL" "$FRONTEND_DIR"
fi

echo "--- 2. Injecting Environment Configurations ---"

echo "Copying inference-orchestrator .env..."
cp "$CONFIG_PATH/inference-orchestrator/.env" "python-ecosystem/inference-orchestrator/.env"

echo "Copying rag-pipeline .env..."
cp "$CONFIG_PATH/rag-pipeline/.env" "python-ecosystem/rag-pipeline/.env"

echo "Copying web-frontend .env..."
# Using the variable ensures we target the folder defined at the top
cp "$CONFIG_PATH/web-frontend/.env" "$FRONTEND_DIR/.env"


echo "--- 3. Building Java Artifacts (mvn clean package) ---"
(cd "$JAVA_DIR" && mvn clean package -DskipTests)

echo "--- 4. MCP Servers jar update ---"
cp "$MCP_SERVERS_JAR_PATH" python-ecosystem/inference-orchestrator/codecrow-vcs-mcp-1.0.jar

echo "--- 4.1. Platform MCP jar update ---"
if [ -f "$PLATFORM_MCP_JAR_PATH" ]; then
    cp "$PLATFORM_MCP_JAR_PATH" python-ecosystem/inference-orchestrator/codecrow-platform-mcp-1.0.jar
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