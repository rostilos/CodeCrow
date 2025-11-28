# Getting Started

## Prerequisites

- Docker 20.10+
- Docker Compose v2.0+
- 4GB+ available RAM
- 10GB+ disk space

Optional (for local development):
- Java 17+
- Maven 3.8+
- Node.js 18+ (with Bun or npm)
- Python 3.10+

## Quick Start

### 1. Clone Repository

```bash
git clone <repository-url>
cd codecrow
```

### 2. Configure Services

Copy sample configuration files and update credentials:

```bash
# Copy docker-compose configuration
cp deployment/docker-compose-sample.yml deployment/docker-compose.yml

# Copy Java configuration
cp deployment/config/java-shared/application.properties.sample \
   deployment/config/java-shared/application.properties

# Copy Python service configurations
cp deployment/config/mcp-client/.env.sample \
   deployment/config/mcp-client/.env

cp deployment/config/rag-pipeline/.env.sample \
   deployment/config/rag-pipeline/.env

cp deployment/config/web-frontend/.env.sample \
   deployment/config/web-frontend/.env
```

### 3. Update Required Credentials

Edit the following files with your actual credentials:

**deployment/config/java-shared/application.properties**:
```properties
# Generate new secrets (use openssl rand -base64 32)
codecrow.security.jwtSecret=<your-jwt-secret>
codecrow.security.encryption-key=<your-encryption-key>

# Update base URL for your deployment
codecrow.web.base.url=http://localhost:8080
```

**deployment/config/rag-pipeline/.env**:
```bash
# Get API key from https://openrouter.ai/
OPENROUTER_API_KEY=sk-or-v1-your-api-key-here
```

**deployment/config/web-frontend/.env**:
```bash
# Update if deploying to different domain
VITE_API_URL=http://localhost:8081/api
VITE_WEBHOOK_URL=http://localhost:8082
```

### 4. Build and Start Services

Use the automated build script:

```bash
./tools/production-build.sh
```

This script will:
1. Build Java artifacts with Maven
2. Copy MCP servers JAR to Python client
3. Build and start all Docker containers
4. Wait for services to be healthy

Or manually with Docker Compose:

```bash
cd deployment
docker compose up -d --build
```

### 5. Verify Services

Check that all services are running:

```bash
cd deployment
docker compose ps
```

Expected services:
- codecrow-postgres (port 5432)
- codecrow-redis (port 6379)
- codecrow-qdrant (port 6333)
- codecrow-web-application (port 8081)
- codecrow-pipeline-agent (port 8082)
- codecrow-mcp-client (port 8000)
- codecrow-rag-pipeline (port 8001)
- codecrow-web-frontend (port 8080)

### 6. Access Application

Open browser: `http://localhost:8080`

Default admin credentials are created on first startup (check logs or database).

### 7. Configure Bitbucket Integration

1. Log into CodeCrow web interface
2. Create a workspace
3. Create a project and link it to your Bitbucket repository
4. Generate project webhook token
5. Configure Bitbucket webhook:
   - URL: `http://<your-domain>:8082/api/v1/bitbucket-cloud/webhook`
   - Events: Pull Request (created, updated), Repository (push)
   - Add authentication header with project token

## Configuration Overview

### Service Ports

| Service | Port | Access |
|---------|------|--------|
| Frontend | 8080 | Public |
| Web Server API | 8081 | Public |
| Pipeline Agent | 8082 | Webhook only |
| MCP Client | 8000 | Internal only |
| RAG Pipeline | 8001 | Internal only |
| PostgreSQL | 5432 | Internal only |
| Redis | 6379 | Internal only |
| Qdrant | 6333 | Internal only |

### Security Considerations

**Important**: MCP Client (port 8000) must NOT be publicly accessible. It has no authentication as it's designed for internal communication only.

For production:
- Use reverse proxy (nginx, Traefik) with SSL
- Restrict pipeline-agent access to Bitbucket webhook IPs only
- Keep MCP client and RAG pipeline internal
- Use strong JWT and encryption keys
- Enable firewall rules

## Initial Setup Checklist

- [ ] Copy all sample config files
- [ ] Generate new JWT secret
- [ ] Generate new encryption key
- [ ] Set OpenRouter API key
- [ ] Update base URLs for your domain
- [ ] Configure database credentials (if not using defaults)
- [ ] Run production-build.sh or docker compose up
- [ ] Verify all services are healthy
- [ ] Access web interface
- [ ] Create first workspace
- [ ] Create first project
- [ ] Configure Bitbucket webhook
- [ ] Test with sample pull request

## Next Steps

- [Configuration Reference](05-configuration.md) - Detailed configuration options
- [Architecture](03-architecture.md) - Understand system design
- [API Reference](06-api-reference.md) - Integrate with REST API
- [Troubleshooting](11-troubleshooting.md) - Common issues

