# Configuration Reference

Complete configuration guide for all CodeCrow components.

## Configuration Files Overview

```
deployment/config/
├── java-shared/
│   └── application.properties    # Java services configuration
├── mcp-client/
│   └── .env                      # MCP client configuration
├── rag-pipeline/
│   └── .env                      # RAG pipeline configuration
└── web-frontend/
    └── .env                      # Frontend configuration
```

## Java Services Configuration

**File**: `deployment/config/java-shared/application.properties`

Used by: web-server, pipeline-agent

### Security Settings

```properties
# JWT Configuration
codecrow.security.jwtSecret=<base64-encoded-secret>
codecrow.security.jwtExpirationMs=86400000
codecrow.security.projectJwtExpirationMs=7776000000

# Encryption Key (AES)
codecrow.security.encryption-key=<base64-encoded-key>
```

**Generate Secrets**:
```bash
# JWT Secret (256-bit)
openssl rand -base64 32

# Encryption Key (256-bit)
openssl rand -base64 32
```

**Token Expiration**:
- `jwtExpirationMs`: User JWT token validity (default: 24 hours)
- `projectJwtExpirationMs`: Project webhook token validity (default: 3 months)

### Google OAuth (Social Login)

Enable Google Sign-In for user authentication:

```properties
# Google OAuth Client ID (same value in frontend and backend)
codecrow.oauth.google.client-id=your-client-id.apps.googleusercontent.com
```

**Setup Steps**:
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create or select a project
3. Navigate to **APIs & Services → Credentials**
4. Click **Create Credentials → OAuth 2.0 Client ID**
5. Select **Web application**
6. Add **Authorized JavaScript origins**: Your frontend URL(s)
7. Add **Authorized redirect URIs**: Same as JavaScript origins
8. Copy the **Client ID** to both backend and frontend configuration

**Frontend Configuration** (`deployment/config/web-frontend/.env`):
```bash
VITE_GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
```

**Important Notes**:
- Both frontend and backend must use the same Client ID
- Google Sign-In button only appears if `VITE_GOOGLE_CLIENT_ID` is configured
- For production, add your domain to authorized origins
- Users signing in with Google can link to existing accounts with matching email

### Email / SMTP Configuration

Email is required for Two-Factor Authentication (2FA), security notifications, and backup codes.

```properties
# Enable/disable email sending
codecrow.email.enabled=true

# Sender email address and display name
codecrow.email.from=noreply@yourdomain.com
codecrow.email.from-name=CodeCrow
codecrow.email.app-name=CodeCrow

# Frontend URL (for email links)
codecrow.email.frontend-url=https://codecrow.example.com

# SMTP Server Configuration
spring.mail.host=smtp.gmail.com
spring.mail.port=587
spring.mail.username=your-email@gmail.com
spring.mail.password=your-app-password
spring.mail.properties.mail.smtp.auth=true
spring.mail.properties.mail.smtp.starttls.enable=true
```

**Quick Setup (Gmail)**:
1. Enable 2-Step Verification in your Google Account
2. Go to Security → App passwords → Generate new app password
3. Use the 16-character password in `spring.mail.password`

> **📖 For detailed SMTP setup with different providers (Amazon SES, SendGrid, Mailgun, Office 365), see [SMTP_SETUP.md](./SMTP_SETUP.md)**

### Application URLs

```properties
# Web Frontend Base URL
codecrow.web.base.url=https://codecrow.example.com

# MCP Client URL (internal)
codecrow.mcp.client.url=http://host.docker.internal:8000/review

# RAG Pipeline URL (internal)
codecrow.rag.api.url=http://host.docker.internal:8001
codecrow.rag.api.enabled=true
```

**Notes**:
- Use `host.docker.internal` for Docker container-to-host communication
- Use service names for inter-container communication
- For production, use actual hostnames or service discovery

### Database Configuration

Set via environment variables in docker-compose.yml:

```yaml
environment:
  SPRING_DATASOURCE_URL: jdbc:postgresql://postgres:5432/codecrow_ai
  SPRING_DATASOURCE_USERNAME: codecrow_user
  SPRING_DATASOURCE_PASSWORD: codecrow_pass
  SPRING_JPA_HIBERNATE_DDL_AUTO: update
  SPRING_JPA_DATABASE_PLATFORM: org.hibernate.dialect.PostgreSQLDialect
```

**Hibernate DDL Auto Options**:
- `none`: No schema management
- `validate`: Validate schema, no changes
- `update`: Update schema (recommended for development)
- `create`: Drop and recreate schema (data loss!)
- `create-drop`: Create on start, drop on stop

**Production Recommendation**: Use `validate` with managed migrations (Flyway/Liquibase).

### Redis Configuration

```yaml
environment:
  SPRING_SESSION_STORE_TYPE: redis
  SPRING_REDIS_HOST: redis
  SPRING_REDIS_PORT: 6379
```

### File Upload Limits

```properties
spring.servlet.multipart.max-file-size=500MB
spring.servlet.multipart.max-request-size=500MB
```

Adjust based on expected repository archive sizes.

### Async Request Timeout

```properties
spring.mvc.async.request-timeout=-1
```

`-1` = no timeout. Necessary for long-running analysis operations.

### Swagger/OpenAPI

```properties
springdoc.swagger-ui.operationsSorter=method
springdoc.swagger-ui.path=/swagger-ui-custom.html
springdoc.api-docs.path=/api-docs
```

Access Swagger UI at: `http://localhost:8081/swagger-ui-custom.html`

### Logging

```properties
logging.level.org.springframework.security.web.access.ExceptionTranslationFilter=ERROR
logging.level.org.apache.catalina.core.ApplicationDispatcher=ERROR
logging.level.org.apache.catalina.core.StandardHostValve=ERROR
```

**Log Levels**: TRACE, DEBUG, INFO, WARN, ERROR, FATAL

**Add Custom Loggers**:
```properties
logging.level.org.rostilos.codecrow=DEBUG
logging.level.org.hibernate.SQL=DEBUG
```

### Server Ports

Set via environment variables:

```yaml
environment:
  SERVER_PORT: 8081  # web-server
  SERVER_PORT: 8082  # pipeline-agent
```

## MCP Client Configuration

**File**: `deployment/config/mcp-client/.env`

```bash
# Server Port
AI_CLIENT_PORT=8000

# RAG Integration
RAG_ENABLED=true
RAG_API_URL=http://localhost:8001

# Optional: OpenRouter Override
# If not set, uses configuration from pipeline-agent request
OPENROUTER_API_KEY=sk-or-v1-your-api-key
OPENROUTER_MODEL=anthropic/claude-3.5-sonnet
```

**OpenRouter Models**:
- `anthropic/claude-3.5-sonnet` - Recommended for code analysis
- `openai/gpt-4-turbo` - OpenAI GPT-4
- `google/gemini-pro` - Google Gemini
- See https://openrouter.ai/docs for full list

**RAG Settings**:
- `RAG_ENABLED=true`: Enable RAG context retrieval
- `RAG_ENABLED=false`: Disable RAG (analysis without code context)

## RAG Pipeline Configuration

**File**: `deployment/config/rag-pipeline/.env`

### Vector Database

```bash
QDRANT_URL=http://localhost:6333
QDRANT_COLLECTION_PREFIX=codecrow
```

Collections are named: `{prefix}_{project_id}_{branch_name}`

### OpenRouter Configuration

```bash
OPENROUTER_API_KEY=sk-or-v1-your-api-key-here
OPENROUTER_MODEL=openai/text-embedding-3-small
```

**Embedding Models**:
- `openai/text-embedding-3-small` - Fast, cost-effective (default)
- `openai/text-embedding-3-large` - Higher quality, more expensive
- `openai/text-embedding-ada-002` - Legacy model

**Get API Key**: https://openrouter.ai/

### Chunking Configuration

```bash
# Code Files
CHUNK_SIZE=800
CHUNK_OVERLAP=200

# Text Files
TEXT_CHUNK_SIZE=1000
TEXT_CHUNK_OVERLAP=200
```

**Tuning**:
- Larger chunks: More context, fewer embeddings, less granular
- Smaller chunks: More granular, more embeddings, higher cost
- Overlap: Ensures context continuity across chunks

### Retrieval Configuration

```bash
RETRIEVAL_TOP_K=10
SIMILARITY_THRESHOLD=0.7
```

**Top K**: Number of most similar chunks to return  
**Similarity Threshold**: Minimum similarity score (0.0-1.0)

### File Processing

```bash
MAX_FILE_SIZE_BYTES=1048576
```

Files larger than this are skipped (default: 1MB).

### Server Configuration

```bash
SERVER_HOST=0.0.0.0
SERVER_PORT=8001
```

### Cache Directories

```bash
HOME=/tmp
TIKTOKEN_CACHE_DIR=/tmp/.tiktoken_cache
TRANSFORMERS_CACHE=/tmp/.transformers_cache
HF_HOME=/tmp/.huggingface
LLAMA_INDEX_CACHE_DIR=/tmp/.llama_index
```

**Important for Docker**: These should be writable directories.

### Default Exclude Patterns

The RAG pipeline automatically excludes common non-code files:

```
node_modules/**
.venv/**
venv/**
__pycache__/**
*.pyc, *.pyo, *.so, *.dll, *.dylib, *.exe, *.bin
*.jar, *.war, *.class
target/**
build/**
dist/**
.git/**
.idea/**
*.min.js, *.min.css, *.bundle.js
*.lock, package-lock.json, yarn.lock, bun.lockb
```

Additional patterns can be configured per-project (see Project RAG Configuration below).

## Project-Level RAG Configuration

Each project can configure RAG indexing via the web UI or API.

### Configuration Options

| Option | Type | Description |
|--------|------|-------------|
| `enabled` | boolean | Enable/disable RAG indexing for this project |
| `branch` | string | Branch to index (defaults to project's default branch) |
| `excludePatterns` | string[] | Additional paths/patterns to exclude from indexing |

### Exclude Patterns

Project-specific exclude patterns are merged with the default system patterns.

**Supported Pattern Formats**:

| Pattern | Description | Example Matches |
|---------|-------------|-----------------|
| `vendor/**` | Directory with all subdirectories | `vendor/lib/file.php`, `vendor/autoload.php` |
| `app/code/**` | Nested directory pattern | `app/code/Module/Model.php` |
| `*.min.js` | File extension pattern | `script.min.js`, `vendor/lib.min.js` |
| `**/*.generated.ts` | Any directory + file pattern | `src/types.generated.ts` |
| `lib/` | Exact directory prefix | `lib/file.js`, `lib/sub/file.js` |

**Example Configuration** (via API):
```json
{
  "enabled": true,
  "branch": "main",
  "excludePatterns": [
    "vendor/**",
    "lib/**",
    "generated/**",
    "app/design/**",
    "*.min.js",
    "*.map"
  ]
}
```

**Use Cases**:
- Exclude third-party code (`vendor/**`, `node_modules/**`)
- Exclude generated files (`generated/**`, `*.generated.ts`)
- Exclude design/theme files (`app/design/**`)
- Exclude build artifacts (`dist/**`, `build/**`)
- Exclude large data files (`data/**`, `fixtures/**`)

### Configuring via Web UI

1. Navigate to **Project Settings** → **RAG Configuration**
2. Enable RAG indexing with the toggle
3. Set the branch to index (optional)
4. Add exclude patterns:
   - Type pattern in the input field
   - Press Enter or click the **+** button
   - Remove patterns by clicking the **×** on each badge
5. Click **Save Configuration**

### Configuring via API

```http
PUT /api/workspace/{workspaceSlug}/project/{projectNamespace}/rag/config
Authorization: Bearer <token>
Content-Type: application/json

{
  "enabled": true,
  "branch": "main",
  "excludePatterns": ["vendor/**", "lib/**"]
}
```

## Frontend Configuration

**File**: `deployment/config/web-frontend/.env`

```bash
# Backend API URL
VITE_API_URL=http://localhost:8081/api

# Webhook URL (for display in UI)
VITE_WEBHOOK_URL=http://localhost:8082

# Server Port
SERVER_PORT=8080
```

**Production Example**:
```bash
VITE_API_URL=https://api.codecrow.example.com/api
VITE_WEBHOOK_URL=https://webhooks.codecrow.example.com
SERVER_PORT=8080
```

## Docker Compose Configuration

**File**: `deployment/docker-compose.yml`

### PostgreSQL

```yaml
postgres:
  environment:
    POSTGRES_DB: codecrow_ai
    POSTGRES_USER: codecrow_user
    POSTGRES_PASSWORD: codecrow_pass
  volumes:
    - postgres_data:/var/lib/postgresql/data
```

**Change Credentials**: Update all services that connect to PostgreSQL.

### Redis

```yaml
redis:
  image: redis:7-alpine
  volumes:
    - redis_data:/data
```

**Password Protection** (optional):
```yaml
redis:
  command: redis-server --requirepass <password>
```

Then update services:
```yaml
environment:
  SPRING_REDIS_PASSWORD: <password>
```

### Qdrant

```yaml
qdrant:
  image: qdrant/qdrant:latest
  volumes:
    - qdrant_data:/qdrant/storage
```

**Persistence**: All data stored in named volume.

### Resource Limits

```yaml
mcp-client:
  deploy:
    resources:
      limits:
        cpus: '1.0'
        memory: 1G
      reservations:
        cpus: '0.5'
        memory: 512M
```

Adjust based on workload and available resources.

### Health Checks

```yaml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:8081/actuator/health"]
  interval: 30s
  timeout: 10s
  retries: 5
  start_period: 60s
```

**Tuning**:
- `interval`: How often to check
- `timeout`: Max time for health check
- `retries`: Failures before marking unhealthy
- `start_period`: Grace period on startup

### Networks

```yaml
networks:
  codecrow-network:
```

All services on same network can communicate by service name.

### Volumes

```yaml
volumes:
  source_code_tmp:
  postgres_data:
  redis_data:
  qdrant_data:
  web_logs:
  pipeline_agent_logs:
  web_frontend_logs:
  rag_logs:
```

**Persistent**: Data survives container restarts.

**Backup**: Use `docker volume` commands to backup/restore.

## VCS Provider Configuration

CodeCrow supports multiple VCS providers with different connection types. Configure these in `application.properties`:

### Bitbucket Cloud App

For 1-click app installation, configure your Bitbucket OAuth consumer:

```properties
# Bitbucket Cloud App Configuration
codecrow.bitbucket.app.client-id=<your-oauth-consumer-key>
codecrow.bitbucket.app.client-secret=<your-oauth-consumer-secret>
```

**Setup Steps**:
1. Go to Bitbucket Settings → Workspace settings → OAuth consumers → Add consumer
2. Set callback URL to: `https://your-domain.com/api/{workspaceSlug}/integrations/bitbucket-cloud/app/callback`
3. Required permissions:
   - Account: Read
   - Repositories: Read, Write
   - Pull requests: Read, Write  
   - Webhooks: Read and write
4. Copy the Key (client-id) and Secret (client-secret) to `application.properties`

### GitHub OAuth App

For 1-click GitHub integration, configure a GitHub OAuth App:

```properties
# GitHub OAuth App Configuration
codecrow.github.app.client-id=<your-github-client-id>
codecrow.github.app.client-secret=<your-github-client-secret>
```

**Setup Steps**:
1. Go to GitHub → Settings → Developer settings → OAuth Apps → New OAuth App
   - Direct URL: https://github.com/settings/developers
2. Fill in the application details:
   - **Application name**: CodeCrow (or your preferred name)
   - **Homepage URL**: Your frontend URL (e.g., `https://codecrow.example.com`)
   - **Authorization callback URL**: `https://your-api-domain.com/api/{workspaceSlug}/integrations/github/app/callback`
     - Replace `{workspaceSlug}` with your actual workspace slug, or configure your reverse proxy to handle workspace routing
3. Click "Register application"
4. Copy the **Client ID** to `codecrow.github.app.client-id`
5. Click "Generate a new client secret" and copy it to `codecrow.github.app.client-secret`

**OAuth Scopes Requested**:
| Scope | Description |
|-------|-------------|
| `repo` | Full control of private repositories (read/write code, issues, PRs) |
| `read:user` | Read user profile data |
| `read:org` | Read organization membership |

**Important Notes**:
- GitHub OAuth Apps only support ONE callback URL
- For multi-workspace support, use a wildcard approach in your reverse proxy or use a single workspace slug
- Client secrets cannot be viewed again after creation - store them securely
- For GitHub Enterprise Server, contact your admin for OAuth App setup

### Connection Types

| Type | Description | Use Case |
|------|-------------|----------|
| `APP` | OAuth 2.0 App installation | Recommended for teams, workspace-level access |
| `OAUTH_MANUAL` | User-initiated OAuth flow | Individual user connections |
| `PERSONAL_TOKEN` | Personal access token | Bitbucket Server/DC, GitHub, automation |
| `APPLICATION` | Server-to-server OAuth | Background services |

### VCS Provider Settings

```properties
# Enable/disable providers
codecrow.vcs.providers.bitbucket-cloud.enabled=true
codecrow.vcs.providers.bitbucket-server.enabled=false
codecrow.vcs.providers.github.enabled=true
codecrow.vcs.providers.gitlab.enabled=false

# Provider-specific API URLs (for self-hosted instances)
codecrow.vcs.bitbucket-server.api-url=https://bitbucket.your-company.com
codecrow.vcs.gitlab.api-url=https://gitlab.your-company.com
```

### Webhook Configuration

```properties
# Webhook secret for signature verification (optional but recommended)
codecrow.webhooks.secret=<your-webhook-secret>

# Provider-specific webhook endpoints (configured automatically by CodeCrow)
# Bitbucket Cloud: /api/webhooks/bitbucket-cloud/{projectId}
# GitHub: /api/webhooks/github/{projectId}
```

**Webhook Events**:
| Provider | Events |
|----------|--------|
| Bitbucket Cloud | `pullrequest:created`, `pullrequest:updated`, `pullrequest:fulfilled`, `repo:push` |
| GitHub | `pull_request` (opened, synchronize, reopened), `push` |

**GitHub Webhook Security**:
- Webhooks are automatically created when onboarding repositories
- Each project gets a unique webhook secret stored in the database
- Webhook payloads are verified using HMAC-SHA256 signature

## Environment-Specific Configuration

### Development

```properties
# Enable debug logging
logging.level.org.rostilos.codecrow=DEBUG

# Enable SQL logging
spring.jpa.show-sql=true
spring.jpa.properties.hibernate.format_sql=true

# Hot reload
spring.devtools.restart.enabled=true
```

### Production

```properties
# Minimal logging
logging.level.org.rostilos.codecrow=INFO

# Disable SQL logging
spring.jpa.show-sql=false

# Validate schema only
spring.jpa.hibernate.ddl-auto=validate

# Enable actuator with authentication
management.endpoints.web.exposure.include=health,info
management.endpoint.health.show-details=when-authorized
```

**Additional Production Settings**:
- Use HTTPS/SSL
- Enable firewall rules
- Restrict database access
- Use secrets management (Vault, AWS Secrets Manager)
- Enable monitoring and alerting
- Regular backups
- Use reverse proxy (nginx, Traefik)

## Configuration Validation

### Check Configuration

```bash
# Java services
docker logs codecrow-web-application | grep "Started"
docker logs codecrow-pipeline-agent | grep "Started"

# Python services
curl http://localhost:8000/health
curl http://localhost:8001/health

# Frontend
curl http://localhost:8080
```

### Test Database Connection

```bash
docker exec -it codecrow-postgres psql -U codecrow_user -d codecrow_ai -c "SELECT version();"
```

### Test Redis Connection

```bash
docker exec -it codecrow-redis redis-cli ping
```

### Test Qdrant Connection

```bash
curl http://localhost:6333/collections
```

## Troubleshooting Configuration

### Service Won't Start

Check logs:
```bash
docker logs <container-name>
```

Common issues:
- Missing environment variables
- Invalid configuration values
- Database connection failed
- Port already in use

### Configuration Not Applied

Ensure:
- Config file is mounted correctly in docker-compose.yml
- File permissions are correct
- Container is restarted after config change
- No typos in property names

### Secrets Exposed in Logs

Avoid:
```properties
# Don't log passwords
spring.datasource.password=secret
```

Use environment variables and secure secret management in production.

