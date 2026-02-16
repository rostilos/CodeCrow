#!/bin/bash
set -e

MCP_SERVERS_JAR_PATH="java-ecosystem/mcp-servers/vcs-mcp/target/codecrow-vcs-mcp-1.0.jar"
PLATFORM_MCP_JAR_PATH="java-ecosystem/mcp-servers/platform-mcp/target/codecrow-platform-mcp-1.0.jar"
FRONTEND_DIR="frontend"
FRONTEND_BRANCH="epic/CA-1-self-host"
JAVA_DIR="java-ecosystem"
DOCKER_PATH="deployment"
CONFIG_PATH="deployment/config"
NEW_RELIC_CONFIG_DIR="~/newrelic-infra/java-ecosystem"

cd "$(dirname "$0")/../"

echo "--- 1. Ensuring frontend submodule is synchronized ---"
if [ -d "$FRONTEND_DIR" ] && [ ! -f "$FRONTEND_DIR/.git" ]; then
   echo "Stale frontend directory detected (not a submodule). Removing and re-initializing..."
   rm -rf "$FRONTEND_DIR"
   git submodule update --init --remote -- "$FRONTEND_DIR"
elif [ -d "$FRONTEND_DIR" ]; then
   echo "Frontend submodule exists. Updating..."
   git submodule update --remote -- "$FRONTEND_DIR"
else
   echo "Initializing frontend submodule..."
   git submodule update --init --remote -- "$FRONTEND_DIR"
fi
(cd "$FRONTEND_DIR" && git checkout "$FRONTEND_BRANCH" && git pull origin "$FRONTEND_BRANCH")

echo "--- 2. Injecting Environment Configurations ---"

echo "Copying inference-orchestrator .env..."
cp "$CONFIG_PATH/inference-orchestrator/.env" "python-ecosystem/inference-orchestrator/.env"

echo "Copying rag-pipeline .env..."
cp "$CONFIG_PATH/rag-pipeline/.env" "python-ecosystem/rag-pipeline/.env"

echo "Copying web-frontend .env..."
# Using the variable ensures we target the folder defined at the top
cp "$CONFIG_PATH/web-frontend/.env" "$FRONTEND_DIR/.env"


echo "--- 3. Injection NewRelic COnfigurations ---"
cp "$NEW_RELIC_CONFIG_DIR/pipeline-agent/newrelic.jar" "$JAVA_DIR/services/pipeline-agent/newrelic.jar"
cp "$NEW_RELIC_CONFIG_DIR/pipeline-agent/newrelic.yml" "$JAVA_DIR/services/pipeline-agent/newrelic.yml"

cp "$NEW_RELIC_CONFIG_DIR/web-server/newrelic.jar" "$JAVA_DIR/services/web-server/newrelic.jar"
cp "$NEW_RELIC_CONFIG_DIR/web-server/newrelic.yml" "$JAVA_DIR/services/web-server/newrelic.yml"

echo "--- 4. Building Java Artifacts (mvn clean package) ---"
(cd "$JAVA_DIR" && mvn clean package -DskipTests)

echo "--- 5. MCP Servers jar update ---"
cp "$MCP_SERVERS_JAR_PATH" python-ecosystem/inference-orchestrator/codecrow-vcs-mcp-1.0.jar

echo "--- 5.1. Platform MCP jar update ---"
if [ -f "$PLATFORM_MCP_JAR_PATH" ]; then
    cp "$PLATFORM_MCP_JAR_PATH" python-ecosystem/inference-orchestrator/codecrow-platform-mcp-1.0.jar
    echo "Platform MCP JAR copied successfully."
else
    echo "Warning: Platform MCP JAR not found at $PLATFORM_MCP_JAR_PATH"
fi

echo "--- 6. Shutting down existing services cleanly ---"
cd "$DOCKER_PATH"
docker compose down --remove-orphans

echo "--- 7. Building Docker images and starting services ---"
docker compose up -d --build --wait

echo "--- Deployment Complete! Services are up and healthy. ---"
docker compose ps