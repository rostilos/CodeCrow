#!/bin/bash

# =============================================================================
# CodeCrow Database Dump Script
# =============================================================================
# Creates a PostgreSQL dump from the codecrow-postgres container
# Usage: ./db-dump.sh [output-directory]
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DEFAULT_OUTPUT_DIR="$PROJECT_ROOT/tools/environment/dumps"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Container name
CONTAINER_NAME="codecrow-postgres"

echo -e "${BLUE}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║           CodeCrow Database Dump Tool                          ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Check if container is running
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo -e "${RED}Error: Container '${CONTAINER_NAME}' is not running.${NC}"
    echo -e "${YELLOW}Start the containers first with: cd deployment && docker compose up -d${NC}"
    exit 1
fi

# Prompt for database credentials
echo -e "${YELLOW}Enter database credentials:${NC}"
read -p "Database name [codecrow_ai]: " DB_NAME
DB_NAME="${DB_NAME:-codecrow_ai}"

read -p "Database user [codecrow_user]: " DB_USER
DB_USER="${DB_USER:-codecrow_user}"

# Output configuration
OUTPUT_DIR="${1:-$DEFAULT_OUTPUT_DIR}"
TIMESTAMP=$(date '+%d%b%Y_%H%M%S')
DUMP_FILENAME="${DB_NAME}_dump_${TIMESTAMP}.sql"

# Create output directory if it doesn't exist
mkdir -p "$OUTPUT_DIR"

echo ""
echo -e "${YELLOW}Container:${NC} $CONTAINER_NAME"
echo -e "${YELLOW}Database:${NC} $DB_NAME"
echo -e "${YELLOW}User:${NC} $DB_USER"
echo -e "${YELLOW}Output:${NC} $OUTPUT_DIR/$DUMP_FILENAME"
echo ""

# Step 1: Create dump inside container
echo -e "${BLUE}[1/3]${NC} Creating database dump inside container..."
docker exec "$CONTAINER_NAME" pg_dump -h localhost -U "$DB_USER" -d "$DB_NAME" > "$OUTPUT_DIR/$DUMP_FILENAME"

if [ $? -ne 0 ]; then
    echo -e "${RED}Error: Failed to create database dump.${NC}"
    exit 1
fi

# Step 2: Verify dump file
DUMP_SIZE=$(du -h "$OUTPUT_DIR/$DUMP_FILENAME" | cut -f1)
DUMP_LINES=$(wc -l < "$OUTPUT_DIR/$DUMP_FILENAME")

echo -e "${BLUE}[2/3]${NC} Verifying dump file..."
echo -e "       Size: ${GREEN}$DUMP_SIZE${NC}"
echo -e "       Lines: ${GREEN}$DUMP_LINES${NC}"

# Step 3: Create latest symlink
echo -e "${BLUE}[3/3]${NC} Creating 'latest' symlink..."
ln -sf "$DUMP_FILENAME" "$OUTPUT_DIR/latest.sql"

echo ""
echo -e "${GREEN}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                    Dump Complete!                              ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "Dump file: ${BLUE}$OUTPUT_DIR/$DUMP_FILENAME${NC}"
echo -e "Latest:    ${BLUE}$OUTPUT_DIR/latest.sql${NC}"
echo ""
echo -e "${YELLOW}To restore this dump:${NC}"
echo -e "  docker exec -i $CONTAINER_NAME psql -U $DB_USER -d $DB_NAME < $OUTPUT_DIR/$DUMP_FILENAME"
