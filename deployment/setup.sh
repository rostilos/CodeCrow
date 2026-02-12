#!/usr/bin/env bash
# ============================================================================
# CodeCrow Self-Host Setup Wizard
# ============================================================================
# This script generates all required configuration files from .sample templates,
# auto-generates cryptographic secrets, and walks you through the settings
# you need to fill in.
#
# Usage:
#   ./setup.sh              — interactive first-time setup
#   ./setup.sh --keep       — regenerate secrets but keep existing config values
#   ./setup.sh --reset      — discard existing configs and start fresh
#
# Requirements: openssl, sed, bash ≥ 4
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="$SCRIPT_DIR/config"
MARKER="$CONFIG_DIR/.setup-complete"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m' # No Color

# ── Helpers ────────────────────────────────────────────────────────────────

info()    { echo -e "${BLUE}ℹ${NC}  $*"; }
success() { echo -e "${GREEN}✔${NC}  $*"; }
warn()    { echo -e "${YELLOW}⚠${NC}  $*"; }
error()   { echo -e "${RED}✖${NC}  $*" >&2; }
header()  { echo -e "\n${BOLD}${CYAN}── $* ──${NC}\n"; }

prompt() {
  local var_name="$1" prompt_text="$2" default="${3:-}"
  if [[ -n "$default" ]]; then
    echo -en "${BOLD}$prompt_text${NC} ${DIM}[$default]${NC}: "
    read -r value
    eval "$var_name=\"${value:-$default}\""
  else
    echo -en "${BOLD}$prompt_text${NC}: "
    read -r value
    eval "$var_name=\"$value\""
  fi
}

prompt_secret() {
  local var_name="$1" prompt_text="$2"
  echo -en "${BOLD}$prompt_text${NC}: "
  read -rs value
  echo
  eval "$var_name=\"$value\""
}

prompt_yn() {
  local prompt_text="$1" default="${2:-n}"
  local yn
  if [[ "$default" == "y" ]]; then
    echo -en "${BOLD}$prompt_text${NC} ${DIM}[Y/n]${NC}: "
  else
    echo -en "${BOLD}$prompt_text${NC} ${DIM}[y/N]${NC}: "
  fi
  read -r yn
  yn="${yn:-$default}"
  [[ "$yn" =~ ^[Yy] ]]
}

generate_base64_key() {
  openssl rand -base64 32
}

generate_hex_key() {
  openssl rand -hex 32
}

# ── Pre-flight checks ─────────────────────────────────────────────────────

check_requirements() {
  local missing=()
  command -v openssl &>/dev/null || missing+=("openssl")
  command -v sed &>/dev/null     || missing+=("sed")
  command -v docker &>/dev/null  || missing+=("docker")
  
  if [[ ${#missing[@]} -gt 0 ]]; then
    error "Missing required tools: ${missing[*]}"
    error "Please install them and re-run this script."
    exit 1
  fi
  
  # Check docker compose (v2)
  if ! docker compose version &>/dev/null 2>&1; then
    warn "Docker Compose v2 not found. Make sure 'docker compose' works."
  fi
}

# ── Config file management ─────────────────────────────────────────────────

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

# ── sed helper (cross-platform) ───────────────────────────────────────────

# Replace a value in a .properties file:  key=old → key=new
sed_prop() {
  local file="$1" key="$2" value="$3"
  # Escape special sed characters in value
  local escaped_value
  escaped_value=$(printf '%s\n' "$value" | sed -e 's/[\/&]/\\&/g')
  sed -i "s|^${key}=.*|${key}=${escaped_value}|" "$file"
}

# Replace a value in a .env file:  KEY=old → KEY=new
sed_env() {
  local file="$1" key="$2" value="$3"
  local escaped_value
  escaped_value=$(printf '%s\n' "$value" | sed -e 's/[\/&]/\\&/g')
  sed -i "s|^${key}=.*|${key}=${escaped_value}|" "$file"
}

# ── Main setup flow ───────────────────────────────────────────────────────

main() {
  echo -e "\n${BOLD}${CYAN}╔══════════════════════════════════════════════════╗${NC}"
  echo -e "${BOLD}${CYAN}║         CodeCrow Self-Host Setup Wizard          ║${NC}"
  echo -e "${BOLD}${CYAN}╚══════════════════════════════════════════════════╝${NC}\n"
  
  check_requirements
  
  # Parse flags
  local mode="interactive"
  for arg in "$@"; do
    case "$arg" in
      --keep)  mode="keep" ;;
      --reset) mode="reset" ;;
      --help|-h)
        echo "Usage: ./setup.sh [--keep | --reset]"
        echo "  (no flags)  — interactive first-time setup"
        echo "  --keep      — regenerate secrets, keep other config values"
        echo "  --reset     — discard existing configs and start fresh"
        exit 0
        ;;
    esac
  done
  
  # Check if already configured
  if [[ -f "$MARKER" && "$mode" == "interactive" ]]; then
    warn "Setup was already completed previously."
    if ! prompt_yn "Re-run setup? This will overwrite existing configs"; then
      info "Aborted. Use --keep to update secrets without overwriting."
      exit 0
    fi
    mode="reset"
  fi
  
  local overwrite="false"
  [[ "$mode" == "reset" || ! -f "$MARKER" ]] && overwrite="true"
  
  # ─── Step 1: Copy sample files ───────────────────────────────────────
  
  header "Step 1/6 — Generating config files from templates"
  copy_samples "$overwrite"
  
  # Config file paths
  local JAVA_PROPS="$CONFIG_DIR/java-shared/application.properties"
  local ORCH_ENV="$CONFIG_DIR/inference-orchestrator/.env"
  local RAG_ENV="$CONFIG_DIR/rag-pipeline/.env"
  local FRONT_ENV="$CONFIG_DIR/web-frontend/.env"
  
  # ─── Step 2: Generate secrets ────────────────────────────────────────
  
  header "Step 2/6 — Generating cryptographic secrets"
  
  local JWT_SECRET ENCRYPTION_KEY INTERNAL_SECRET SERVICE_SECRET PG_PASSWORD
  JWT_SECRET=$(generate_base64_key)
  ENCRYPTION_KEY=$(generate_base64_key)
  INTERNAL_SECRET=$(generate_hex_key)
  SERVICE_SECRET=$(generate_hex_key)
  PG_PASSWORD=$(generate_hex_key | head -c 24)
  
  # Java shared — secrets
  sed_prop "$JAVA_PROPS" "codecrow.security.jwtSecret" "$JWT_SECRET"
  sed_prop "$JAVA_PROPS" "codecrow.security.encryption-key" "$ENCRYPTION_KEY"
  sed_prop "$JAVA_PROPS" "codecrow.security.encryption-key-old" "$ENCRYPTION_KEY"
  sed_prop "$JAVA_PROPS" "codecrow.internal.api.secret" "$INTERNAL_SECRET"
  sed_prop "$JAVA_PROPS" "codecrow.rag.api.secret" "$SERVICE_SECRET"
  
  # Inference orchestrator — secrets (must match)
  sed_env "$ORCH_ENV" "SERVICE_SECRET" "$SERVICE_SECRET"
  sed_env "$ORCH_ENV" "INTERNAL_API_SECRET" "$INTERNAL_SECRET"
  
  # RAG pipeline — secrets (must match)
  sed_env "$RAG_ENV" "SERVICE_SECRET" "$SERVICE_SECRET"
  
  success "JWT signing key generated & injected"
  success "Encryption key generated & injected"
  success "Internal API secret generated & synchronized across 2 services"
  success "Service secret generated & synchronized across 3 services"
  
  # ─── Step 3: URLs / Domain ──────────────────────────────────────────
  
  header "Step 3/6 — Domain & URLs"
  
  info "Configure the URLs where CodeCrow will be accessible."
  info "For local testing, keep the defaults (localhost)."
  echo
  
  local DOMAIN API_PORT FRONTEND_PORT WEBHOOK_PORT
  prompt DOMAIN "Domain or IP (e.g., codecrow.example.com or localhost)" "localhost"
  
  local PROTOCOL="http"
  if [[ "$DOMAIN" != "localhost" && "$DOMAIN" != "127.0.0.1" ]]; then
    if prompt_yn "Use HTTPS?"; then
      PROTOCOL="https"
    fi
  fi
  
  prompt API_PORT "API server port" "8081"
  prompt FRONTEND_PORT "Frontend port" "8080"
  prompt WEBHOOK_PORT "Pipeline agent (webhook) port" "8082"
  
  local BASE_URL="${PROTOCOL}://${DOMAIN}"
  local API_URL="${BASE_URL}:${API_PORT}"
  local FRONTEND_URL="${BASE_URL}:${FRONTEND_PORT}"
  local WEBHOOK_URL="${BASE_URL}:${WEBHOOK_PORT}"
  
  # If using standard ports with reverse proxy
  if [[ "$PROTOCOL" == "https" ]]; then
    if prompt_yn "Using a reverse proxy (standard ports 80/443)?"; then
      API_URL="${PROTOCOL}://${DOMAIN}"
      FRONTEND_URL="${PROTOCOL}://${DOMAIN}"
      WEBHOOK_URL="${PROTOCOL}://${DOMAIN}"
      info "Assuming reverse proxy routes to internal ports."
    fi
  fi
  
  # Java shared
  sed_prop "$JAVA_PROPS" "codecrow.web.base.url" "$API_URL"
  sed_prop "$JAVA_PROPS" "codecrow.frontend-url" "$FRONTEND_URL"
  sed_prop "$JAVA_PROPS" "codecrow.frontend.url" "$FRONTEND_URL"
  sed_prop "$JAVA_PROPS" "codecrow.email.frontend-url" "$FRONTEND_URL"
  sed_prop "$JAVA_PROPS" "codecrow.webhook.base-url" "$WEBHOOK_URL"
  
  # Frontend
  sed_env "$FRONT_ENV" "VITE_API_URL" "${API_URL}/api"
  sed_env "$FRONT_ENV" "VITE_WEBHOOK_URL" "$WEBHOOK_URL"
  sed_env "$FRONT_ENV" "VITE_APP_URL" "$FRONTEND_URL"
  
  success "URLs configured: API=$API_URL  Frontend=$FRONTEND_URL"
  
  # ─── Step 4: VCS Integrations ───────────────────────────────────────
  
  header "Step 4/6 — Version Control System (VCS) Integrations"
  
  info "Configure at least ONE VCS provider to use CodeCrow."
  info "You can add more later by editing application.properties."
  echo
  
  local HAS_VCS=false
  
  # ── GitHub App ──
  if prompt_yn "Configure GitHub App integration?"; then
    HAS_VCS=true
    echo
    info "Create a GitHub App at: https://github.com/settings/apps/new"
    info "  Callback URL: ${API_URL}/api/integrations/github/app/callback"
    info "  Webhook URL:  ${WEBHOOK_URL}/api/integrations/github/app/webhook"
    info "  Permissions:  Contents(Read), Pull requests(Read/Write),"
    info "                Issues(Read/Write), Webhooks(Read/Write)"
    echo
    
    local GH_APP_ID GH_APP_SLUG GH_WEBHOOK_SECRET GH_PEM_PATH
    prompt GH_APP_ID "GitHub App ID (numeric)"
    prompt GH_APP_SLUG "GitHub App slug (url-friendly name)"
    prompt GH_WEBHOOK_SECRET "GitHub webhook secret"
    prompt GH_PEM_PATH "Path to private key .pem file" "./config/java-shared/github-private-key/github-app.pem"
    
    sed_prop "$JAVA_PROPS" "codecrow.github.app.id" "$GH_APP_ID"
    sed_prop "$JAVA_PROPS" "codecrow.github.app.slug" "$GH_APP_SLUG"
    sed_prop "$JAVA_PROPS" "codecrow.github.app.webhook-secret" "$GH_WEBHOOK_SECRET"
    sed_prop "$JAVA_PROPS" "codecrow.github.app.private-key-path" "/app/config/github-app-private-key.pem"
    
    if [[ ! -f "$SCRIPT_DIR/$GH_PEM_PATH" && "$GH_PEM_PATH" == *".pem" ]]; then
      warn "Private key not found at $GH_PEM_PATH"
      warn "Make sure to place it there before running docker compose."
    fi
    
    success "GitHub App configured"
  fi
  
  # ── GitLab OAuth ──
  if prompt_yn "Configure GitLab OAuth integration?"; then
    HAS_VCS=true
    echo
    info "Create a GitLab OAuth Application:"
    info "  GitLab.com:     https://gitlab.com/-/user_settings/applications"
    info "  Self-hosted:    https://your-gitlab/user_settings/applications"
    info "  Redirect URI:   ${API_URL}/api/integrations/gitlab/app/callback"
    info "  Scopes:         api, read_user, read_repository, write_repository"
    echo
    
    local GL_CLIENT_ID GL_CLIENT_SECRET GL_BASE_URL
    prompt GL_CLIENT_ID "GitLab Application ID"
    prompt_secret GL_CLIENT_SECRET "GitLab Secret"
    prompt GL_BASE_URL "GitLab base URL (leave empty for gitlab.com)" ""
    
    sed_prop "$JAVA_PROPS" "codecrow.gitlab.oauth.client-id" "$GL_CLIENT_ID"
    sed_prop "$JAVA_PROPS" "codecrow.gitlab.oauth.client-secret" "$GL_CLIENT_SECRET"
    [[ -n "$GL_BASE_URL" ]] && sed_prop "$JAVA_PROPS" "codecrow.gitlab.oauth.base-url" "$GL_BASE_URL"
    
    success "GitLab OAuth configured"
  fi
  
  # ── Bitbucket Cloud ──
  if prompt_yn "Configure Bitbucket Cloud OAuth integration?"; then
    HAS_VCS=true
    echo
    info "Create a Bitbucket OAuth Consumer:"
    info "  Bitbucket → Workspace settings → OAuth consumers → Add consumer"
    info "  Callback URL: ${API_URL}/api/{workspaceSlug}/integrations/bitbucket-cloud/app/callback"
    info "  Permissions:  Account(Read), Repos(Read/Write),"
    info "                Pull Requests(Read/Write), Webhooks(Read/Write)"
    echo
    
    local BB_CLIENT_ID BB_CLIENT_SECRET
    prompt BB_CLIENT_ID "Bitbucket OAuth Key (client-id)"
    prompt_secret BB_CLIENT_SECRET "Bitbucket OAuth Secret (client-secret)"
    
    sed_prop "$JAVA_PROPS" "codecrow.bitbucket.app.client-id" "$BB_CLIENT_ID"
    sed_prop "$JAVA_PROPS" "codecrow.bitbucket.app.client-secret" "$BB_CLIENT_SECRET"
    
    success "Bitbucket Cloud configured"
  fi
  
  if [[ "$HAS_VCS" == "false" ]]; then
    warn "No VCS provider configured. CodeCrow requires at least one."
    warn "You can configure one later by editing:"
    warn "  $JAVA_PROPS"
  fi
  
  # ─── Step 5: Embedding Provider ─────────────────────────────────────
  
  header "Step 5/6 — Embedding Provider (for RAG)"
  
  info "CodeCrow needs an embedding model for the RAG pipeline."
  info "Choose one:"
  echo -e "  ${BOLD}1)${NC} Ollama (local, free — requires Ollama installed on host)"
  echo -e "  ${BOLD}2)${NC} OpenRouter (cloud API — requires API key, ~\$0.006/1M tokens)"
  echo
  
  local EMBED_CHOICE
  prompt EMBED_CHOICE "Embedding provider [1/2]" "1"
  
  if [[ "$EMBED_CHOICE" == "2" ]]; then
    local OR_API_KEY OR_MODEL
    prompt_secret OR_API_KEY "OpenRouter API key (sk-or-v1-...)"
    prompt OR_MODEL "OpenRouter embedding model" "qwen/qwen3-embedding-8b"
    
    sed_env "$RAG_ENV" "EMBEDDING_PROVIDER" "openrouter"
    sed_env "$RAG_ENV" "OPENROUTER_API_KEY" "$OR_API_KEY"
    sed_env "$RAG_ENV" "OPENROUTER_MODEL" "$OR_MODEL"
    
    success "OpenRouter embeddings configured"
  else
    local OLLAMA_URL OLLAMA_MODEL
    prompt OLLAMA_URL "Ollama base URL" "http://host.docker.internal:11434"
    prompt OLLAMA_MODEL "Ollama embedding model" "qwen3-embedding:0.6b"
    
    sed_env "$RAG_ENV" "EMBEDDING_PROVIDER" "ollama"
    sed_env "$RAG_ENV" "OLLAMA_BASE_URL" "$OLLAMA_URL"
    sed_env "$RAG_ENV" "OLLAMA_EMBEDDING_MODEL" "$OLLAMA_MODEL"
    
    success "Ollama embeddings configured"
    info "Make sure Ollama is running: ollama serve"
    info "Pull the model: ollama pull $OLLAMA_MODEL"
  fi
  
  # ─── Step 6: Optional Features ──────────────────────────────────────
  
  header "Step 6/6 — Optional Features"
  
  # SMTP
  echo -e "${BOLD}Email / SMTP${NC}"
  info "Email is used for: password reset, 2FA codes, notifications."
  info "You can skip this — the platform works without it."
  echo
  
  if prompt_yn "Configure SMTP email?"; then
    local SMTP_HOST SMTP_PORT SMTP_USER SMTP_PASS SMTP_FROM
    prompt SMTP_HOST "SMTP host (e.g., smtp.gmail.com)"
    prompt SMTP_PORT "SMTP port" "587"
    prompt SMTP_USER "SMTP username"
    prompt_secret SMTP_PASS "SMTP password"
    prompt SMTP_FROM "Sender email address" "noreply@${DOMAIN}"
    
    sed_prop "$JAVA_PROPS" "codecrow.email.enabled" "true"
    sed_prop "$JAVA_PROPS" "codecrow.email.from" "$SMTP_FROM"
    sed_prop "$JAVA_PROPS" "spring.mail.host" "$SMTP_HOST"
    sed_prop "$JAVA_PROPS" "spring.mail.port" "$SMTP_PORT"
    sed_prop "$JAVA_PROPS" "spring.mail.username" "$SMTP_USER"
    sed_prop "$JAVA_PROPS" "spring.mail.password" "$SMTP_PASS"
    
    success "SMTP configured"
  else
    sed_prop "$JAVA_PROPS" "codecrow.email.enabled" "false"
    success "Email disabled — platform will work without SMTP"
  fi
  
  echo
  
  # Google OAuth
  echo -e "${BOLD}Google Social Login${NC}"
  info "Optional: let users sign in with their Google account."
  echo
  
  if prompt_yn "Configure Google OAuth?" "n"; then
    local GOOGLE_CLIENT_ID
    prompt GOOGLE_CLIENT_ID "Google OAuth Client ID"
    
    sed_prop "$JAVA_PROPS" "codecrow.oauth.google.client-id" "$GOOGLE_CLIENT_ID"
    sed_env "$FRONT_ENV" "VITE_GOOGLE_CLIENT_ID" "$GOOGLE_CLIENT_ID"
    
    success "Google OAuth configured"
  else
    info "Skipped — users will register with email/password"
  fi
  
  # ─── Done ───────────────────────────────────────────────────────────
  
  # Write marker
  date -Iseconds > "$MARKER"
  
  echo -e "\n${BOLD}${GREEN}╔══════════════════════════════════════════════════╗${NC}"
  echo -e "${BOLD}${GREEN}║            Setup Complete! 🎉                    ║${NC}"
  echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════════════╝${NC}\n"
  
  echo -e "Generated config files:"
  echo -e "  ${DIM}$JAVA_PROPS${NC}"
  echo -e "  ${DIM}$ORCH_ENV${NC}"
  echo -e "  ${DIM}$RAG_ENV${NC}"
  echo -e "  ${DIM}$FRONT_ENV${NC}"
  echo
  echo -e "${BOLD}Next steps:${NC}"
  echo -e "  1. Review the generated config files if needed"
  echo -e "  2. Build and start all services:"
  echo
  echo -e "     ${CYAN}./production-build.sh${NC}"
  echo
  echo -e "  Or manually:"
  echo
  echo -e "     ${CYAN}cd $(dirname "$SCRIPT_DIR")${NC}"
  echo -e "     ${CYAN}cd java-ecosystem && mvn clean package -DskipTests && cd ..${NC}"
  echo -e "     ${CYAN}cd deployment && docker compose up -d --build${NC}"
  echo
  echo -e "  3. Open ${BOLD}${FRONTEND_URL}${NC} in your browser"
  echo
  echo -e "${DIM}To re-run: ./setup.sh --reset${NC}"
}

main "$@"
