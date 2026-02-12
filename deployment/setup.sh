#!/usr/bin/env bash
# ============================================================================
# CodeCrow Self-Host Setup
# ============================================================================
# This script:
#   1. Copies .sample config files → live config files (skips if they exist)
#   2. Auto-generates all cryptographic secrets
#   3. Synchronises shared secrets across services
#   4. Randomises the default database password
#
# After running this script, build and start the services, then finish
# configuration in the browser (Setup Wizard).
#
# Usage:
#   ./setup.sh              — first-time setup (won't overwrite existing configs)
#   ./setup.sh --reset      — discard existing configs and start fresh
#
# Requirements: openssl, sed, bash ≥ 4
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="$SCRIPT_DIR/config"

# ── Colours ────────────────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

info()    { echo -e "${BLUE}ℹ${NC}  $*"; }
success() { echo -e "${GREEN}✔${NC}  $*"; }
warn()    { echo -e "${YELLOW}⚠${NC}  $*"; }
error()   { echo -e "${RED}✖${NC}  $*" >&2; }
header()  { echo -e "\n${BOLD}${CYAN}── $* ──${NC}\n"; }

# ── Helpers ────────────────────────────────────────────────────────────────

generate_base64() { openssl rand -base64 32; }
generate_hex()    { openssl rand -hex 32; }

# Replace key=value in a .properties or .env file
set_value() {
  local file="$1" key="$2" value="$3"
  local escaped
  escaped=$(printf '%s\n' "$value" | sed -e 's/[\/&]/\\&/g')
  sed -i "s|^${key}=.*|${key}=${escaped}|" "$file"
}

# ── Pre-flight checks ─────────────────────────────────────────────────────

check_requirements() {
  local missing=()
  command -v openssl &>/dev/null || missing+=("openssl")
  command -v sed     &>/dev/null || missing+=("sed")

  if [[ ${#missing[@]} -gt 0 ]]; then
    error "Missing required tools: ${missing[*]}"
    error "Install them and re-run this script."
    exit 1
  fi
}

# ── Copy sample files ─────────────────────────────────────────────────────

copy_samples() {
  local overwrite="$1"

  local samples=(
    "java-shared/application.properties.sample:java-shared/application.properties"
    "inference-orchestrator/.env.sample:inference-orchestrator/.env"
    "rag-pipeline/.env.sample:rag-pipeline/.env"
    "web-frontend/.env.sample:web-frontend/.env"
  )

  for entry in "${samples[@]}"; do
    local src="${entry%%:*}"
    local dst="${entry##*:}"
    local src_path="$CONFIG_DIR/$src"
    local dst_path="$CONFIG_DIR/$dst"

    if [[ ! -f "$src_path" ]]; then
      error "Sample file not found: $src_path"
      exit 1
    fi

    if [[ -f "$dst_path" && "$overwrite" != "true" ]]; then
      info "Keeping existing: $dst"
    else
      cp "$src_path" "$dst_path"
      success "Created: $dst"
    fi
  done
}

# ── Main ───────────────────────────────────────────────────────────────────

main() {
  echo -e "\n${BOLD}${CYAN}╔══════════════════════════════════════════════════╗${NC}"
  echo -e "${BOLD}${CYAN}║         CodeCrow Self-Host Setup                 ║${NC}"
  echo -e "${BOLD}${CYAN}╚══════════════════════════════════════════════════╝${NC}\n"

  check_requirements

  # Parse flags
  local overwrite="false"
  for arg in "$@"; do
    case "$arg" in
      --reset)  overwrite="true" ;;
      --help|-h)
        echo "Usage: ./setup.sh [--reset]"
        echo "  (no flags)  — first-time setup (keeps existing config files)"
        echo "  --reset     — discard existing configs and regenerate everything"
        exit 0
        ;;
    esac
  done

  # ─── Step 1: Copy sample files ───────────────────────────────────────

  header "Step 1/3 — Config files"
  copy_samples "$overwrite"

  # Config file paths
  local JAVA_PROPS="$CONFIG_DIR/java-shared/application.properties"
  local ORCH_ENV="$CONFIG_DIR/inference-orchestrator/.env"
  local RAG_ENV="$CONFIG_DIR/rag-pipeline/.env"

  # ─── Step 2: Generate & inject secrets ───────────────────────────────

  header "Step 2/3 — Generating cryptographic secrets"

  # JWT signing key
  local JWT_SECRET
  JWT_SECRET=$(generate_base64)
  set_value "$JAVA_PROPS" "codecrow.security.jwtSecret" "$JWT_SECRET"
  success "JWT signing key"

  # AES encryption key (set both current + old to the same value on first run)
  local ENCRYPTION_KEY
  ENCRYPTION_KEY=$(generate_base64)
  set_value "$JAVA_PROPS" "codecrow.security.encryption-key"     "$ENCRYPTION_KEY"
  set_value "$JAVA_PROPS" "codecrow.security.encryption-key-old" "$ENCRYPTION_KEY"
  success "AES encryption key"

  # Internal API secret  (web-server <-> inference-orchestrator)
  local INTERNAL_SECRET
  INTERNAL_SECRET=$(generate_hex)
  set_value "$JAVA_PROPS" "codecrow.internal.api.secret" "$INTERNAL_SECRET"
  set_value "$ORCH_ENV"   "INTERNAL_API_SECRET"          "$INTERNAL_SECRET"
  success "Internal API secret  (synced: application.properties + inference-orchestrator/.env)"

  # Service / RAG secret (web-server <-> inference-orchestrator <-> rag-pipeline)
  local SERVICE_SECRET
  SERVICE_SECRET=$(generate_hex)
  set_value "$JAVA_PROPS" "codecrow.rag.api.secret" "$SERVICE_SECRET"
  set_value "$ORCH_ENV"   "SERVICE_SECRET"          "$SERVICE_SECRET"
  set_value "$RAG_ENV"    "SERVICE_SECRET"          "$SERVICE_SECRET"
  success "Service secret       (synced: application.properties + inference-orchestrator/.env + rag-pipeline/.env)"

  # ─── Step 3: Database password ───────────────────────────────────────

  header "Step 3/3 — Database password"

  local PG_PASSWORD
  PG_PASSWORD=$(generate_hex | head -c 24)
  local DC_FILE="$SCRIPT_DIR/docker-compose.yml"

  if [[ -f "$DC_FILE" ]]; then
    sed -i "s|POSTGRES_PASSWORD: codecrow_pass|POSTGRES_PASSWORD: ${PG_PASSWORD}|g"                   "$DC_FILE"
    sed -i "s|SPRING_DATASOURCE_PASSWORD: codecrow_pass|SPRING_DATASOURCE_PASSWORD: ${PG_PASSWORD}|g" "$DC_FILE"
    success "Database password randomised in docker-compose.yml"
  else
    warn "docker-compose.yml not found — set the database password manually."
  fi

  # ─── Done ────────────────────────────────────────────────────────────

  echo -e "\n${BOLD}${GREEN}╔══════════════════════════════════════════════════╗${NC}"
  echo -e "${BOLD}${GREEN}║            Setup complete! 🎉                    ║${NC}"
  echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════════════╝${NC}\n"

  echo -e "Generated config files:"
  echo -e "  ${DIM}config/java-shared/application.properties${NC}"
  echo -e "  ${DIM}config/inference-orchestrator/.env${NC}"
  echo -e "  ${DIM}config/rag-pipeline/.env${NC}"
  echo -e "  ${DIM}config/web-frontend/.env${NC}"
  echo

  echo -e "${BOLD}Next steps:${NC}"
  echo
  echo -e "  1. ${CYAN}Build & start all services:${NC}"
  echo
  echo -e "     ${BOLD}./production-build.sh${NC}"
  echo
  echo -e "  2. ${CYAN}Open your browser:${NC}"
  echo
  echo -e "     ${BOLD}http://localhost:8080${NC}"
  echo
  echo -e "  3. ${CYAN}Register the first account${NC} — it automatically"
  echo -e "     becomes the ${BOLD}Site Admin${NC}."
  echo
  echo -e "  4. ${CYAN}Follow the Setup Wizard${NC} in the browser to configure:"
  echo -e "     • Base URLs (API, frontend, webhook)"
  echo -e "     • Embedding provider (Ollama or OpenRouter)"
  echo -e "     • VCS integration (GitHub, GitLab, or Bitbucket)"
  echo -e "     • Optional: SMTP, Google OAuth, LLM sync keys"
  echo
  echo -e "${DIM}To re-run from scratch: ./setup.sh --reset${NC}"
}

main "$@"
