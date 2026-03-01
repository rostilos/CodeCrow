#!/usr/bin/env bash
# ============================================================================
# CodeCrow Self-Host Setup
# ============================================================================
# This script:
#   1. Copies sample config files → live config files
#   2. Auto-generates all cryptographic secrets + database credentials
#   3. Interactively configures the embedding LLM provider (Ollama or OpenRouter)
#   4. Creates deployment/.env with DB credentials + shared docker-compose secrets
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

  # Config files
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
      info "Keeping existing: config/$dst"
    else
      cp "$src_path" "$dst_path"
      success "Created: config/$dst"
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

  # ─── Step 1: Copy sample config files ─────────────────────────────

  header "Step 1/3 — Copy sample config files"
  copy_samples "$overwrite"

  # Config file paths
  local JAVA_PROPS="$CONFIG_DIR/java-shared/application.properties"
  local ORCH_ENV="$CONFIG_DIR/inference-orchestrator/.env"
  local RAG_ENV="$CONFIG_DIR/rag-pipeline/.env"
  local ROOT_ENV="$SCRIPT_DIR/.env"

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

  # Internal API secret  (web-server <-> inference-orchestrator <-> rag-pipeline)
  local INTERNAL_SECRET
  INTERNAL_SECRET=$(generate_hex)
  set_value "$JAVA_PROPS" "codecrow.internal.api.secret" "$INTERNAL_SECRET"

  # Database credentials — auto-generated, stored only in .env
  local DB_PASSWORD
  DB_PASSWORD=$(generate_base64)

  # Write all shared secrets to .env (docker-compose.yml reads this automatically)
  cat > "$ROOT_ENV" <<EOF
INTERNAL_API_SECRET=${INTERNAL_SECRET}
POSTGRES_DB=codecrow_ai
POSTGRES_USER=codecrow_user
POSTGRES_PASSWORD=${DB_PASSWORD}
EOF
  success "Internal API secret  (synced: application.properties + .env)"
  success "Database credentials (auto-generated in .env)"

  # Service / RAG secret (inference-orchestrator <-> rag-pipeline)
  local SERVICE_SECRET
  SERVICE_SECRET=$(generate_hex)
  set_value "$JAVA_PROPS" "codecrow.rag.api.secret" "$SERVICE_SECRET"
  set_value "$ORCH_ENV"   "SERVICE_SECRET"          "$SERVICE_SECRET"
  set_value "$RAG_ENV"    "SERVICE_SECRET"          "$SERVICE_SECRET"
  success "Service secret       (synced: application.properties + inference-orchestrator/.env + rag-pipeline/.env)"

  # Qdrant API key — lives only in deployment/.env
  # Docker Compose injects it as QDRANT__SERVICE__API_KEY into the Qdrant container
  # and as QDRANT_API_KEY into the rag-pipeline container.
  local QDRANT_API_KEY
  QDRANT_API_KEY=$(generate_hex)
  echo "QDRANT_API_KEY=${QDRANT_API_KEY}" >> "$ROOT_ENV"
  success "Qdrant API key       (written to .env — injected into containers by docker-compose)"

  # ─── Step 3: Configure embedding provider ─────────────────────────

  header "Step 3/3 — Embedding LLM configuration"

  echo -e "CodeCrow uses text embeddings for the RAG (code-context) pipeline."
  echo -e "You must choose an embedding provider:\n"
  echo -e "  ${BOLD}1)${NC} ${CYAN}Ollama${NC}   — local, free, requires Ollama running on the host"
  echo -e "  ${BOLD}2)${NC} ${CYAN}OpenRouter${NC} — cloud, paid, requires an API key\n"

  local PROVIDER_CHOICE=""
  while [[ "$PROVIDER_CHOICE" != "1" && "$PROVIDER_CHOICE" != "2" ]]; do
    read -rp "$(echo -e "${BOLD}Select provider [1/2]:${NC} ")" PROVIDER_CHOICE
    if [[ "$PROVIDER_CHOICE" != "1" && "$PROVIDER_CHOICE" != "2" ]]; then
      warn "Please enter 1 or 2."
    fi
  done

  if [[ "$PROVIDER_CHOICE" == "1" ]]; then
    # ── Ollama ──────────────────────────────────────────────────────────
    set_value "$RAG_ENV" "EMBEDDING_PROVIDER" "ollama"
    success "Embedding provider: Ollama"

    local OLLAMA_URL=""
    read -rp "$(echo -e "${BOLD}Ollama base URL${NC} [${DIM}http://host.docker.internal:11434${NC}]: ")" OLLAMA_URL
    OLLAMA_URL="${OLLAMA_URL:-http://host.docker.internal:11434}"
    set_value "$RAG_ENV" "OLLAMA_BASE_URL" "$OLLAMA_URL"
    success "Ollama URL: $OLLAMA_URL"

    local OLLAMA_MODEL=""
    read -rp "$(echo -e "${BOLD}Ollama embedding model${NC} [${DIM}qwen3-embedding:0.6b${NC}]: ")" OLLAMA_MODEL
    OLLAMA_MODEL="${OLLAMA_MODEL:-qwen3-embedding:0.6b}"
    set_value "$RAG_ENV" "OLLAMA_EMBEDDING_MODEL" "$OLLAMA_MODEL"
    success "Ollama model: $OLLAMA_MODEL"

    echo
    info "Make sure Ollama is running and the model is pulled:"
    echo -e "   ${DIM}ollama pull ${OLLAMA_MODEL}${NC}"

  else
    # ── OpenRouter ──────────────────────────────────────────────────────
    set_value "$RAG_ENV" "EMBEDDING_PROVIDER" "openrouter"
    success "Embedding provider: OpenRouter"

    local OR_KEY=""
    while [[ -z "$OR_KEY" ]]; do
      read -rp "$(echo -e "${BOLD}OpenRouter API key${NC} (required): ")" OR_KEY
      if [[ -z "$OR_KEY" ]]; then
        warn "API key cannot be empty. Get one at https://openrouter.ai/"
      fi
    done
    set_value "$RAG_ENV" "OPENROUTER_API_KEY" "$OR_KEY"
    success "OpenRouter API key: ${OR_KEY:0:12}…"

    local OR_MODEL=""
    read -rp "$(echo -e "${BOLD}OpenRouter model${NC} [${DIM}qwen/qwen3-embedding-8b${NC}]: ")" OR_MODEL
    OR_MODEL="${OR_MODEL:-qwen/qwen3-embedding-8b}"
    set_value "$RAG_ENV" "OPENROUTER_MODEL" "$OR_MODEL"
    success "OpenRouter model: $OR_MODEL"
  fi

  # ─── Done ────────────────────────────────────────────────────────────

  echo -e "\n${BOLD}${GREEN}╔══════════════════════════════════════════════════╗${NC}"
  echo -e "${BOLD}${GREEN}║            Setup complete! 🎉                    ║${NC}"
  echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════════════╝${NC}\n"

  echo -e "Generated files:"
  echo -e "  ${DIM}.env${NC}                                        (DB credentials + INTERNAL_API_SECRET + QDRANT_API_KEY)"
  echo -e "  ${DIM}config/java-shared/application.properties${NC}"
  echo -e "  ${DIM}config/inference-orchestrator/.env${NC}"
  echo -e "  ${DIM}config/rag-pipeline/.env${NC}                    (embedding provider configured)"
  echo -e "  ${DIM}config/web-frontend/.env${NC}"
  echo

  info "Database credentials were auto-generated in ${BOLD}.env${NC}."
  echo -e "   To change them later, edit ${DIM}POSTGRES_USER${NC}, ${DIM}POSTGRES_PASSWORD${NC}, and ${DIM}POSTGRES_DB${NC}"
  echo -e "   in ${DIM}deployment/.env${NC}. Docker Compose reads them automatically."
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
  echo -e "     • VCS integration (GitHub, GitLab, or Bitbucket)"
  echo -e "     • Optional: SMTP, Google OAuth, LLM sync keys"
  echo
  echo -e "${DIM}To re-run from scratch: ./setup.sh --reset${NC}"
}

main "$@"
