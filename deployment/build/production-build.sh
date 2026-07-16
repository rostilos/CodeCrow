#!/bin/bash
set -e

MCP_SERVERS_JAR_PATH="java-ecosystem/mcp-servers/vcs-mcp/target/codecrow-vcs-mcp-1.0.jar"
PLATFORM_MCP_JAR_PATH="java-ecosystem/mcp-servers/platform-mcp/target/codecrow-platform-mcp-1.0.jar"
FRONTEND_DIR="frontend"
FRONTEND_BRANCH="main"
JAVA_DIR="java-ecosystem"
DOCKER_PATH="deployment"
CONFIG_PATH="deployment/config"

# A pre-release V2.13.0 migration was executed on some persistent development
# databases before the final migration was committed. Repair only that exact
# known history row; never disable Flyway validation or repair unknown drift.
repair_known_flyway_drift() {
    local history_table
    local migration_state

    history_table=$(docker compose exec -T postgres sh -c \
        'psql -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
        "SELECT to_regclass('\''public.flyway_schema_history'\'')"')
    if [ -z "$history_table" ]; then
        return
    fi

    migration_state=$(docker compose exec -T postgres sh -c \
        'psql -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -F "|" -c \
        "SELECT checksum, script FROM flyway_schema_history WHERE version = '\''2.13.0'\'' AND success"')

    if [ "$migration_state" != "-509251171|V2.13.0__quarantine_unverified_github_app_connections.sql" ]; then
        return
    fi

    echo "Repairing known pre-release Flyway V2.13.0 history drift..."
    docker compose exec -T postgres sh -c \
        'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' <<'SQL'
BEGIN;

ALTER TABLE vcs_connection
    ADD COLUMN IF NOT EXISTS github_installation_request_id VARCHAR(128),
    ADD COLUMN IF NOT EXISTS github_installation_requester_id VARCHAR(128),
    ADD COLUMN IF NOT EXISTS github_installation_request_snapshot TEXT,
    ADD COLUMN IF NOT EXISTS github_installation_request_started_at TIMESTAMP,
    ADD COLUMN IF NOT EXISTS github_binding_verified_at TIMESTAMP;

CREATE UNIQUE INDEX IF NOT EXISTS uq_vcs_connection_new_verified_github_installation
    ON vcs_connection (installation_id)
    WHERE github_binding_verified_at IS NOT NULL;

UPDATE flyway_schema_history
SET description = 'add github installation request binding',
    script = 'V2.13.0__add_github_installation_request_binding.sql',
    checksum = 933531837
WHERE version = '2.13.0'
  AND checksum = -509251171
  AND script = 'V2.13.0__quarantine_unverified_github_app_connections.sql'
  AND success;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM flyway_schema_history
        WHERE version = '2.13.0'
          AND checksum = 933531837
          AND script = 'V2.13.0__add_github_installation_request_binding.sql'
          AND success
    ) THEN
        RAISE EXCEPTION 'Known Flyway V2.13.0 history row was not updated';
    END IF;
END $$;

COMMIT;
SQL
}

show_deployment_failure() {
    echo "Deployment failed. Container status:"
    docker compose ps --all || true
    echo "Recent application logs:"
    docker compose logs --tail=120 --no-color web-server pipeline-agent rag-pipeline inference-orchestrator || true
}

cd "$(dirname "$0")/../../"

# echo "--- 1. Ensuring frontend submodule is synchronized ---"
# if [ -d "$FRONTEND_DIR" ] && [ ! -f "$FRONTEND_DIR/.git" ]; then
#    echo "Stale frontend directory detected (not a submodule). Removing and re-initializing..."
#    rm -rf "$FRONTEND_DIR"
#    git submodule update --init -- "$FRONTEND_DIR"
# elif [ ! -d "$FRONTEND_DIR" ]; then
#    echo "Initializing frontend submodule..."
#    git submodule update --init -- "$FRONTEND_DIR"
# else
#    echo "Frontend submodule exists."
# fi
# echo "Fetching latest from origin and resetting to origin/$FRONTEND_BRANCH..."
# (cd "$FRONTEND_DIR" && git fetch origin "$FRONTEND_BRANCH" && git reset --hard "origin/$FRONTEND_BRANCH")
# echo "Frontend at: $(cd "$FRONTEND_DIR" && git log --oneline -1)"

echo "--- 2. Injecting Environment Configurations ---"

echo "Copying inference-orchestrator .env..."
cp "$CONFIG_PATH/inference-orchestrator/.env" "python-ecosystem/inference-orchestrator/src/.env"

echo "Copying rag-pipeline .env..."
cp "$CONFIG_PATH/rag-pipeline/.env" "python-ecosystem/rag-pipeline/.env"

echo "Copying web-frontend .env..."
# Using the variable ensures we target the folder defined at the top
cp "$CONFIG_PATH/web-frontend/.env" "$FRONTEND_DIR/.env"


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
docker compose down --remove-orphans

echo "--- 6. Building Docker images and starting services ---"
docker compose build
docker compose up -d --wait postgres
repair_known_flyway_drift

if ! docker compose up -d --wait; then
    show_deployment_failure
    exit 1
fi

echo "--- Deployment Complete! Services are up and healthy. ---"
docker compose ps
