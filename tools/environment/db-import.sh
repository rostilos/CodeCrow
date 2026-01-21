#!/bin/bash

# =============================================================================
# CodeCrow Database Import Script
# =============================================================================
# Imports a PostgreSQL dump into the codecrow-postgres container
# Usage: ./db-import.sh <dump-file>
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DEFAULT_DUMPS_DIR="$PROJECT_ROOT/tools/environment/dumps"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Container name
CONTAINER_NAME="codecrow-postgres"

echo -e "${BLUE}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║           CodeCrow Database Import Tool                        ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Check if container is running
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo -e "${RED}Error: Container '${CONTAINER_NAME}' is not running.${NC}"
    echo -e "${YELLOW}Start the containers first with: cd deployment && docker compose up -d${NC}"
    exit 1
fi

# Get dump file path
DUMP_FILE="$1"

if [ -z "$DUMP_FILE" ]; then
    echo -e "${YELLOW}Available dump files:${NC}"
    if [ -d "$DEFAULT_DUMPS_DIR" ]; then
        ls -la "$DEFAULT_DUMPS_DIR"/*.sql 2>/dev/null || echo "  No dump files found in $DEFAULT_DUMPS_DIR"
    fi
    echo ""
    read -p "Enter path to dump file: " DUMP_FILE
fi

# Check if dump file exists
if [ ! -f "$DUMP_FILE" ]; then
    echo -e "${RED}Error: Dump file '$DUMP_FILE' not found.${NC}"
    exit 1
fi

# Prompt for database credentials
echo ""
echo -e "${YELLOW}Enter database credentials:${NC}"
read -p "Database name [codecrow_ai]: " DB_NAME
DB_NAME="${DB_NAME:-codecrow_ai}"

read -p "Database user [codecrow_user]: " DB_USER
DB_USER="${DB_USER:-codecrow_user}"

read -sp "Database password: " DB_PASS
echo ""

if [ -z "$DB_PASS" ]; then
    echo -e "${RED}Error: Password is required.${NC}"
    exit 1
fi

# Display info
DUMP_SIZE=$(du -h "$DUMP_FILE" | cut -f1)
echo ""
echo -e "${YELLOW}Container:${NC} $CONTAINER_NAME"
echo -e "${YELLOW}Database:${NC} $DB_NAME"
echo -e "${YELLOW}User:${NC} $DB_USER"
echo -e "${YELLOW}Dump file:${NC} $DUMP_FILE ($DUMP_SIZE)"
echo ""

# Confirm before proceeding
echo -e "${RED}WARNING: This will DROP and RECREATE the database '$DB_NAME'.${NC}"
echo -e "${RED}All existing data will be lost!${NC}"
read -p "Are you sure you want to continue? (yes/no): " CONFIRM

if [ "$CONFIRM" != "yes" ]; then
    echo -e "${YELLOW}Operation cancelled.${NC}"
    exit 0
fi

echo ""

# Step 1: Copy dump file into container
echo -e "${BLUE}[1/4]${NC} Copying dump file into container..."
docker cp "$DUMP_FILE" "$CONTAINER_NAME:/tmp/import_dump.sql"

# Step 2: Terminate existing connections
echo -e "${BLUE}[2/4]${NC} Terminating existing connections to '$DB_NAME'..."
docker exec -e PGPASSWORD="$DB_PASS" "$CONTAINER_NAME" psql -h localhost -U "$DB_USER" -d postgres -c "
SELECT pg_terminate_backend(pg_stat_activity.pid)
FROM pg_stat_activity
WHERE pg_stat_activity.datname = '$DB_NAME'
  AND pid <> pg_backend_pid();
" 2>/dev/null || true

# Step 3: Drop and recreate database
echo -e "${BLUE}[3/4]${NC} Dropping and recreating database '$DB_NAME'..."
docker exec -e PGPASSWORD="$DB_PASS" "$CONTAINER_NAME" psql -h localhost -U "$DB_USER" -d postgres -c "DROP DATABASE IF EXISTS $DB_NAME;"
docker exec -e PGPASSWORD="$DB_PASS" "$CONTAINER_NAME" psql -h localhost -U "$DB_USER" -d postgres -c "CREATE DATABASE $DB_NAME;"

# Step 4: Import dump
echo -e "${BLUE}[4/4]${NC} Importing dump file (this may take a while)..."
docker exec -e PGPASSWORD="$DB_PASS" "$CONTAINER_NAME" psql -h localhost -U "$DB_USER" -d "$DB_NAME" -f /tmp/import_dump.sql -q

# Cleanup
docker exec "$CONTAINER_NAME" rm -f /tmp/import_dump.sql

echo ""
echo -e "${GREEN}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                    Import Complete!                            ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "Database '${BLUE}$DB_NAME${NC}' has been restored from '${BLUE}$DUMP_FILE${NC}'"
