# Java Ecosystem

The Java ecosystem is built as a multi-module Maven project with shared libraries and runnable services.

## Project Structure

```
java-ecosystem/
├── pom.xml                    # Parent POM
├── libs/                      # Shared libraries
│   ├── core/                  # Core models and persistence
│   ├── security/              # Security utilities
│   └── vcs-client/            # VCS API client
├── services/                  # Runnable applications
│   ├── pipeline-agent/        # Analysis processing engine
│   └── web-server/            # REST API backend
└── mcp-servers/               # MCP server implementations
    └── bitbucket-mcp/         # Bitbucket MCP tools
```

## Maven Parent Configuration

**GroupId**: `org.rostilos.codecrow`  
**Version**: `1.0`  
**Java Version**: 17  
**Spring Boot**: 3.2.5

### Key Dependencies
- Spring Boot Starter (Web, Data JPA, Security)
- PostgreSQL Driver
- JWT (jjwt)
- Lombok
- Jackson
- Hibernate
- Redis

## Shared Libraries

### codecrow-core

Core library containing domain models, repositories, and common services.

**Package Structure**:
```
org.rostilos.codecrow.core/
├── model/                     # JPA entities
│   ├── user/                 # User, Role
│   ├── workspace/            # Workspace, WorkspaceMember
│   ├── project/              # Project, ProjectMember, ProjectToken
│   ├── branch/               # Branch, BranchFile, BranchIssue
│   ├── analysis/             # CodeAnalysis, AnalysisLock, RagIndexStatus
│   ├── ai/                   # AIConnection
│   └── permission/           # PermissionTemplate, ProjectPermissionAssignment
├── dto/                      # Data transfer objects
├── persistence/              # Spring Data repositories
├── service/                  # Common business logic services
└── utils/                    # Utility classes
```

**Key Entities**:

- **User**: System users with authentication credentials
- **Workspace**: Top-level organizational unit
- **Project**: Repository representation, linked to VCS
- **Branch**: Git branch tracking with analysis status
- **BranchIssue**: Issues found in a branch
- **CodeAnalysis**: Pull request analysis record
- **CodeAnalysisIssue**: Issues found in PR analysis
- **PullRequest**: PR metadata and status
- **ProjectToken**: Authentication tokens for webhooks
- **AIConnection**: LLM provider configuration
- **RagIndexStatus**: RAG indexing status per branch

**Repositories**:
All entities have corresponding Spring Data JPA repositories with custom query methods.

**Services**:
- User management
- Workspace operations
- Project CRUD
- Branch tracking
- Issue management
- Analysis coordination

### codecrow-security

Security library for authentication and authorization.

**Features**:
- JWT token generation and validation
- Password encryption (BCrypt)
- Role-based access control
- Permission checking utilities
- Security context helpers
- Encryption utilities (AES)

**Key Classes**:
- `JwtTokenProvider`: Generate and validate JWT tokens
- `JwtAuthenticationFilter`: Extract and validate tokens from requests
- `SecurityConfig`: Spring Security configuration
- `PermissionService`: Check user permissions
- `EncryptionUtil`: Encrypt/decrypt sensitive data

**Configuration Properties**:
```properties
codecrow.security.jwtSecret=<secret>
codecrow.security.jwtExpirationMs=86400000
codecrow.security.projectJwtExpirationMs=7776000000
codecrow.security.encryption-key=<key>
```

### codecrow-vcs-client

VCS platform API client library (currently Bitbucket Cloud).

**Features**:
- Bitbucket REST API client
- Repository operations
- Pull request data fetching
- Diff retrieval
- File content access
- Branch information
- OAuth2 authentication support

**Key Classes**:
- `BitbucketClient`: Main API client
- `BitbucketAuthService`: Authentication handling
- `RepositoryService`: Repository operations
- `PullRequestService`: PR operations
- `DiffService`: Diff parsing and retrieval

**Usage Example**:
```java
BitbucketClient client = new BitbucketClient(credentials);
PullRequest pr = client.getPullRequest(workspace, repo, prId);
List<DiffEntry> diffs = client.getDiff(workspace, repo, prId);
String content = client.getFileContent(workspace, repo, branch, path);
```

## Services

### pipeline-agent

Analysis processing engine and API gateway between VCS and analysis components.

**Port**: 8082

**Responsibilities**:
- Receive and validate webhooks from Bitbucket
- Coordinate analysis workflow
- Fetch repository data via VCS client
- Send analysis requests to MCP client
- Trigger RAG indexing
- Process and store analysis results
- Update issue statuses

**Package Structure**:
```
org.rostilos.codecrow.pipelineagent/
├── bitbucket/
│   ├── controller/           # Webhook endpoints
│   ├── service/              # Analysis orchestration
│   └── dto/                  # Bitbucket-specific DTOs
├── generic/
│   ├── controller/           # Health checks
│   └── service/              # Common services
└── config/                   # Configuration classes
```

**Key Components**:

**WebhookController**:
- Endpoint: `/api/v1/bitbucket-cloud/webhook`
- Validates webhook signatures
- Authenticates using project tokens
- Routes to appropriate analysis service

**BranchAnalysisService**:
- Processes branch merge events
- Triggers RAG indexing for first analysis
- Fetches branch issues for re-analysis
- Updates issue resolved status

**PullRequestAnalysisService**:
- Processes PR created/updated events
- Fetches PR metadata and diffs
- Includes previous PR issues if reanalysis
- Creates/updates CodeAnalysis records

**AnalysisLockService**:
- Prevents concurrent analysis of same repository
- Uses database-level locks
- Ensures data consistency

**MCPClientService**:
- HTTP client for MCP client API
- Sends analysis requests
- Handles timeouts and retries

**RAGClientService**:
- HTTP client for RAG pipeline API
- Triggers indexing and updates
- Queries for code context

**Configuration**:
```properties
codecrow.mcp.client.url=http://host.docker.internal:8000/review
codecrow.rag.api.url=http://host.docker.internal:8001
codecrow.rag.api.enabled=true
spring.mvc.async.request-timeout=-1
```

**Endpoints**:
- `POST /api/v1/bitbucket-cloud/webhook` - Webhook receiver
- `GET /actuator/health` - Health check

### web-server

Main backend REST API for web interface.

**Port**: 8081

**Responsibilities**:
- User authentication and management
- Workspace CRUD operations
- Project management
- Analysis results retrieval
- Issue browsing and filtering
- VCS integration management
- AI connection configuration
- Permission management

**Package Structure**:
```
org.rostilos.codecrow.webserver/
├── controller/
│   ├── auth/                 # Authentication
│   ├── user/                 # User management
│   ├── workspace/            # Workspace operations
│   ├── project/              # Project CRUD
│   ├── analysis/             # Analysis results, issues
│   ├── vcs/                  # VCS integration
│   ├── ai/                   # AI connections
│   └── permission/           # Permissions
├── service/                  # Business logic
├── dto/                      # Request/Response DTOs
├── exception/                # Exception handling
└── config/                   # Security, Swagger config
```

**Key Endpoints**:

**Authentication** (`/api/auth`):
- `POST /register` - User registration
- `POST /login` - User login
- `POST /logout` - User logout
- `GET /me` - Current user info

**Workspaces** (`/api/workspaces`):
- `GET /` - List user's workspaces
- `POST /` - Create workspace
- `GET /{id}` - Get workspace details
- `PUT /{id}` - Update workspace
- `DELETE /{id}` - Delete workspace
- `POST /{id}/members` - Add member
- `DELETE /{id}/members/{userId}` - Remove member

**Projects** (`/api/projects`):
- `GET /workspace/{workspaceId}` - List workspace projects
- `POST /workspace/{workspaceId}` - Create project
- `GET /{id}` - Get project details
- `PUT /{id}` - Update project
- `DELETE /{id}` - Delete project
- `POST /{id}/tokens` - Generate webhook token
- `GET /{id}/statistics` - Project statistics

**Analysis** (`/api/analysis`):
- `GET /project/{projectId}` - List project analyses
- `GET /{id}` - Get analysis details
- `GET /{id}/issues` - Get analysis issues
- `GET /pull-request/{prId}` - Get PR analysis

**Issues** (`/api/issues`):
- `GET /branch/{branchId}` - Branch issues
- `GET /project/{projectId}/active` - Active issues
- `GET /{id}` - Issue details

**VCS Integration** (`/api/vcs/bitbucket`):
- `POST /connect` - Connect Bitbucket account
- `GET /repositories` - List accessible repositories
- `POST /verify-webhook` - Verify webhook configuration

**AI Connections** (`/api/ai`):
- `GET /` - List AI connections
- `POST /` - Create AI connection
- `PUT /{id}` - Update AI connection
- `DELETE /{id}` - Delete AI connection

**Configuration**:
```properties
server.port=8081
spring.datasource.url=jdbc:postgresql://postgres:5432/codecrow_ai
spring.jpa.hibernate.ddl-auto=update
spring.session.store-type=redis
springdoc.swagger-ui.path=/swagger-ui-custom.html
```

**Security**:
- JWT-based authentication
- Role-based authorization
- Permission checks on endpoints
- CORS configuration
- Session management via Redis

**Swagger/OpenAPI**:
Available at `/swagger-ui-custom.html` when running.

## MCP Servers

### bitbucket-mcp

Java-based MCP server providing Bitbucket tools for private repository access.

**Responsibilities**:
- Provide MCP tools for Bitbucket operations
- Access private repositories securely
- Support MCP protocol for LLM integration

**Build Output**:
JAR file: `codecrow-mcp-servers-1.0.jar`  
Copied to: `python-ecosystem/mcp-client/`

**MCP Tools Provided**:
- `get_repository_info` - Repository metadata
- `get_file_content` - File content retrieval
- `list_directory` - Directory listing
- `get_commit_info` - Commit details
- `search_code` - Code search in repository

**Usage**:
MCP client loads this JAR and executes tools via MCP protocol when needed during analysis.

## Building

### Build All Modules

```bash
cd java-ecosystem
mvn clean package -DskipTests
```

### Build Specific Module

```bash
cd java-ecosystem/services/web-server
mvn clean package
```

### Run Locally (Development)

**Web Server**:
```bash
cd java-ecosystem/services/web-server
mvn spring-boot:run
```

**Pipeline Agent**:
```bash
cd java-ecosystem/services/pipeline-agent
mvn spring-boot:run
```

## Testing

```bash
# Run all tests
mvn test

# Run specific module tests
cd services/web-server
mvn test

# Skip tests during build
mvn clean package -DskipTests
```

## Common Issues

### Build Fails - Dependency Resolution
Ensure Maven can access Maven Central. Check `~/.m2/settings.xml`.

### Port Already in Use
Change port in `application.properties` or stop conflicting process.

### Database Connection Failed
Verify PostgreSQL is running and credentials are correct.

### JWT Token Issues
Ensure `jwtSecret` is set and consistent across restarts.

## Development Tips

- Use IDE (IntelliJ IDEA recommended) with Lombok plugin
- Enable annotation processing for Lombok
- Use Spring Boot DevTools for hot reload
- Check logs in `/app/logs/` directory when running in Docker
- Use debug ports (5005, 5006) for remote debugging

