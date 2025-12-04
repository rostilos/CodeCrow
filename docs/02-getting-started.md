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

# Google OAuth (optional - for social login)
VITE_GOOGLE_CLIENT_ID=your-google-client-id
```

### 4. Configure Google OAuth (Optional)

Google OAuth enables users to sign in/sign up with their Google accounts. To enable this feature:

#### Step 1: Create Google Cloud Project
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select an existing one
3. Navigate to **APIs & Services → Credentials**

#### Step 2: Create OAuth 2.0 Client ID
1. Click **Create Credentials → OAuth 2.0 Client ID**
2. Select **Web application** as the application type
3. Configure the following:
   - **Name**: CodeCrow (or your preferred name)
   - **Authorized JavaScript origins**: 
     - `http://localhost:8080` (for local development)
     - `https://your-domain.com` (for production)
   - **Authorized redirect URIs**: Same as JavaScript origins
4. Click **Create** and copy the **Client ID**

#### Step 3: Configure Application
Add the Google Client ID to your configuration files:

**Backend (deployment/config/java-shared/application.properties)**:
```properties
codecrow.oauth.google.client-id=your-google-client-id.apps.googleusercontent.com
```

**Frontend (deployment/config/web-frontend/.env)**:
```bash
VITE_GOOGLE_CLIENT_ID=your-google-client-id.apps.googleusercontent.com
```

> **Note**: Both frontend and backend must use the same Google Client ID. The Google Sign-In button will only appear if `VITE_GOOGLE_CLIENT_ID` is configured.

### 5. Build and Start Services

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

### 6. Verify Services

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

### 7. Access Application

Open browser: `http://localhost:8080`

Default admin credentials are created on first startup (check logs or database).

### 8. Configure VCS Integration

CodeCrow supports multiple VCS providers and connection types:

#### Option A: Bitbucket Cloud App (Recommended)

The Bitbucket Cloud App provides 1-click integration with automatic webhook setup:

1. Log into CodeCrow web interface
2. Navigate to **Settings → Integrations**
3. Click **Install App** on the Bitbucket Cloud card
4. You'll be redirected to Atlassian to authorize the app
5. Select the Bitbucket workspace(s) to install
6. After installation, you're redirected back to CodeCrow
7. **Step 1**: Select repositories to onboard
8. **Step 2**: Configure AI connection (select existing or create new)
9. Complete setup - projects are created automatically with webhooks configured

Benefits:
- No manual webhook configuration required
- Automatic OAuth2 token refresh
- Workspace-level access to all repositories
- AI connection setup integrated into onboarding flow
- Simplified repository selection

#### Option B: Manual OAuth Connection

For more granular control, use manual OAuth connections:

1. Log into CodeCrow web interface
2. Create a workspace
3. Navigate to **Settings → Code Hosting**
4. Add a new Bitbucket Cloud connection
5. Create a project using one of these methods:

   **New Project (Step-by-step wizard)**:
   - Click "New Project" on the Projects page
   - **Step 1**: Select VCS connection and repository
   - **Step 2**: Enter project name and description
   - **Step 3**: Select or create AI connection
   
   **Import Project (from existing connection)**:
   - Click "Import Project" dropdown on the Projects page
   - Select a VCS connection from the list
   - Follow the step-by-step wizard (same as above)

6. Generate project webhook token
7. Configure Bitbucket webhook manually:
   - URL: `http://<your-domain>:8082/api/v1/bitbucket-cloud/webhook`
   - Events: Pull Request (created, updated), Repository (push)
   - Add authentication header with project token

### 9. Configure AI Connection

AI connections can be configured in multiple ways:

**During Project Creation (Recommended):**
- When creating a new project or importing from VCS, the final step allows you to select an existing AI connection or create a new one
- Supported providers: OpenRouter, OpenAI, Anthropic

**After Project Creation:**
1. Navigate to **Projects → \<Desired project\>**
2. Click on 'settings' in the top right corner
3. Go to AI Connections tab
4. Select the desired connection and click the 'Link to current project' button

#### Supported VCS Providers

| Provider | Status | Connection Types |
|----------|--------|------------------|
| Bitbucket Cloud | ✅ Available | App, OAuth Manual |
| Bitbucket Server/DC | 🔜 Coming Soon | Personal Token |
| GitHub | 🔜 Coming Soon | App, OAuth |
| GitLab | 🔜 Coming Soon | App, OAuth |

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
- [ ] Configure Google OAuth Client ID (optional, for social login)
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

