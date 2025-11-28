# API Reference

Complete REST API documentation for CodeCrow services.

## Base URLs

- **Web Server**: `http://localhost:8081/api`
- **Pipeline Agent**: `http://localhost:8082/api/v1`
- **MCP Client**: `http://localhost:8000` (internal only)
- **RAG Pipeline**: `http://localhost:8001` (internal only)

## Authentication

### JWT Token Authentication

Most endpoints require JWT authentication.

**Obtain Token**:
```http
POST /api/auth/login
Content-Type: application/json

{
  "username": "user@example.com",
  "password": "password123"
}
```

**Response**:
```json
{
  "token": "eyJhbGciOiJIUzI1NiIs...",
  "user": {
    "id": "uuid",
    "username": "user@example.com",
    "email": "user@example.com",
    "roles": ["USER"]
  }
}
```

**Use Token**:
```http
Authorization: Bearer eyJhbGciOiJIUzI1NiIs...
```

### Project Token Authentication

Webhooks use project-specific tokens.

**Generate Token**:
```http
POST /api/projects/{projectId}/tokens
Authorization: Bearer <user-jwt>
```

**Use in Webhook**:
```http
Authorization: Bearer <project-token>
```

## Web Server API

### Authentication Endpoints

#### Register User

```http
POST /api/auth/register
Content-Type: application/json

{
  "username": "newuser",
  "email": "user@example.com",
  "password": "SecurePass123!"
}
```

**Response**: `201 Created`
```json
{
  "id": "uuid",
  "username": "newuser",
  "email": "user@example.com",
  "createdAt": "2024-01-15T10:00:00"
}
```

#### Login

```http
POST /api/auth/login
Content-Type: application/json

{
  "username": "user@example.com",
  "password": "password123"
}
```

**Response**: `200 OK`
```json
{
  "token": "jwt-token",
  "user": { ... }
}
```

#### Get Current User

```http
GET /api/auth/me
Authorization: Bearer <token>
```

**Response**: `200 OK`
```json
{
  "id": "uuid",
  "username": "user@example.com",
  "email": "user@example.com",
  "roles": ["USER"],
  "workspaces": [...]
}
```

#### Logout

```http
POST /api/auth/logout
Authorization: Bearer <token>
```

**Response**: `200 OK`

### Workspace Endpoints

#### List Workspaces

```http
GET /api/workspaces
Authorization: Bearer <token>
```

**Response**: `200 OK`
```json
[
  {
    "id": "workspace-uuid",
    "name": "My Workspace",
    "description": "Company projects",
    "createdAt": "2024-01-01T00:00:00",
    "role": "OWNER",
    "memberCount": 5
  }
]
```

#### Create Workspace

```http
POST /api/workspaces
Authorization: Bearer <token>
Content-Type: application/json

{
  "name": "New Workspace",
  "description": "Description here"
}
```

**Response**: `201 Created`
```json
{
  "id": "uuid",
  "name": "New Workspace",
  "description": "Description here",
  "createdAt": "2024-01-15T10:00:00",
  "ownerId": "user-uuid"
}
```

#### Get Workspace

```http
GET /api/workspaces/{id}
Authorization: Bearer <token>
```

**Response**: `200 OK`
```json
{
  "id": "uuid",
  "name": "My Workspace",
  "description": "...",
  "createdAt": "2024-01-01T00:00:00",
  "members": [
    {
      "userId": "uuid",
      "username": "user1",
      "email": "user1@example.com",
      "role": "OWNER",
      "joinedAt": "2024-01-01T00:00:00"
    }
  ],
  "projects": [...]
}
```

#### Update Workspace

```http
PUT /api/workspaces/{id}
Authorization: Bearer <token>
Content-Type: application/json

{
  "name": "Updated Name",
  "description": "Updated description"
}
```

**Response**: `200 OK`

#### Delete Workspace

```http
DELETE /api/workspaces/{id}
Authorization: Bearer <token>
```

**Response**: `204 No Content`

#### Add Member

```http
POST /api/workspaces/{id}/members
Authorization: Bearer <token>
Content-Type: application/json

{
  "email": "newmember@example.com",
  "role": "MEMBER"
}
```

**Roles**: `OWNER`, `ADMIN`, `MEMBER`, `VIEWER`

**Response**: `201 Created`

#### Remove Member

```http
DELETE /api/workspaces/{id}/members/{userId}
Authorization: Bearer <token>
```

**Response**: `204 No Content`

### Project Endpoints

#### List Projects

```http
GET /api/projects/workspace/{workspaceId}
Authorization: Bearer <token>
```

**Response**: `200 OK`
```json
[
  {
    "id": "project-uuid",
    "name": "My Project",
    "workspaceId": "workspace-uuid",
    "repositoryUrl": "https://bitbucket.org/workspace/repo",
    "defaultBranch": "main",
    "createdAt": "2024-01-01T00:00:00",
    "analysisCount": 45,
    "activeIssues": 12
  }
]
```

#### Create Project

```http
POST /api/projects/workspace/{workspaceId}
Authorization: Bearer <token>
Content-Type: application/json

{
  "name": "New Project",
  "description": "Project description",
  "repositoryUrl": "https://bitbucket.org/workspace/repo",
  "defaultBranch": "main",
  "vcsConnectionId": "vcs-uuid",
  "aiConnectionId": "ai-uuid"
}
```

**Response**: `201 Created`

#### Get Project

```http
GET /api/projects/{id}
Authorization: Bearer <token>
```

**Response**: `200 OK`
```json
{
  "id": "uuid",
  "name": "My Project",
  "description": "...",
  "workspaceId": "workspace-uuid",
  "repositoryUrl": "...",
  "defaultBranch": "main",
  "vcsConnection": { ... },
  "aiConnection": { ... },
  "createdAt": "2024-01-01T00:00:00",
  "branches": [...],
  "statistics": {
    "totalAnalyses": 45,
    "activeIssues": 12,
    "resolvedIssues": 89
  }
}
```

#### Update Project

```http
PUT /api/projects/{id}
Authorization: Bearer <token>
Content-Type: application/json

{
  "name": "Updated Name",
  "description": "Updated description",
  "defaultBranch": "develop"
}
```

**Response**: `200 OK`

#### Delete Project

```http
DELETE /api/projects/{id}
Authorization: Bearer <token>
```

**Response**: `204 No Content`

#### Generate Webhook Token

```http
POST /api/projects/{id}/tokens
Authorization: Bearer <token>
Content-Type: application/json

{
  "name": "Production Webhook",
  "expiresInDays": 90
}
```

**Response**: `201 Created`
```json
{
  "id": "token-uuid",
  "token": "proj_xxxxxxxxxxxxxx",
  "projectId": "project-uuid",
  "name": "Production Webhook",
  "createdAt": "2024-01-15T10:00:00",
  "expiresAt": "2024-04-15T10:00:00"
}
```

#### Get Project Statistics

```http
GET /api/projects/{id}/statistics
Authorization: Bearer <token>
```

**Response**: `200 OK`
```json
{
  "totalAnalyses": 45,
  "pullRequestAnalyses": 40,
  "branchAnalyses": 5,
  "activeIssues": 12,
  "resolvedIssues": 89,
  "issuesBySeverity": {
    "HIGH": 3,
    "MEDIUM": 7,
    "LOW": 2
  },
  "issuesByCategory": {
    "SECURITY": 2,
    "CODE_QUALITY": 8,
    "PERFORMANCE": 2
  },
  "analysisHistory": [
    {
      "date": "2024-01-15",
      "count": 3,
      "issuesFound": 5
    }
  ]
}
```

### Analysis Endpoints

#### List Project Analyses

```http
GET /api/analysis/project/{projectId}?page=0&size=20&sort=createdAt,desc
Authorization: Bearer <token>
```

**Query Parameters**:
- `page`: Page number (0-indexed)
- `size`: Items per page
- `sort`: Sort field and direction

**Response**: `200 OK`
```json
{
  "content": [
    {
      "id": "analysis-uuid",
      "projectId": "project-uuid",
      "pullRequestId": "pr-uuid",
      "status": "COMPLETED",
      "totalIssues": 5,
      "highSeverity": 1,
      "mediumSeverity": 3,
      "lowSeverity": 1,
      "createdAt": "2024-01-15T10:00:00",
      "completedAt": "2024-01-15T10:02:00"
    }
  ],
  "totalElements": 45,
  "totalPages": 3,
  "number": 0,
  "size": 20
}
```

#### Get Analysis

```http
GET /api/analysis/{id}
Authorization: Bearer <token>
```

**Response**: `200 OK`
```json
{
  "id": "uuid",
  "projectId": "project-uuid",
  "pullRequest": {
    "id": "pr-uuid",
    "number": 123,
    "title": "Add authentication",
    "author": "developer"
  },
  "status": "COMPLETED",
  "createdAt": "2024-01-15T10:00:00",
  "completedAt": "2024-01-15T10:02:00",
  "totalIssues": 5,
  "issuesBySeverity": { ... },
  "issuesByCategory": { ... },
  "issues": [...]
}
```

#### Get Analysis Issues

```http
GET /api/analysis/{id}/issues?severity=HIGH&category=SECURITY
Authorization: Bearer <token>
```

**Query Parameters**:
- `severity`: Filter by severity (HIGH, MEDIUM, LOW)
- `category`: Filter by category
- `file`: Filter by file path
- `resolved`: Filter by resolution status

**Response**: `200 OK`
```json
[
  {
    "id": "issue-uuid",
    "analysisId": "analysis-uuid",
    "file": "src/main/java/Auth.java",
    "line": 42,
    "severity": "HIGH",
    "category": "SECURITY",
    "description": "SQL injection vulnerability",
    "suggestion": "Use parameterized queries",
    "codeSnippet": "String query = ...",
    "resolved": false
  }
]
```

### Issue Endpoints

#### Get Branch Issues

```http
GET /api/issues/branch/{branchId}?resolved=false
Authorization: Bearer <token>
```

**Response**: `200 OK`
```json
[
  {
    "id": "issue-uuid",
    "branchId": "branch-uuid",
    "file": "src/service/Payment.java",
    "line": 78,
    "severity": "MEDIUM",
    "category": "CODE_QUALITY",
    "description": "Method too complex",
    "suggestion": "Refactor into smaller methods",
    "resolved": false,
    "createdAt": "2024-01-10T15:00:00"
  }
]
```

#### Get Active Issues

```http
GET /api/issues/project/{projectId}/active
Authorization: Bearer <token>
```

**Response**: `200 OK`
```json
[
  {
    "id": "issue-uuid",
    "branch": "main",
    "file": "src/...",
    "line": 42,
    "severity": "HIGH",
    "category": "SECURITY",
    "description": "...",
    "createdAt": "2024-01-15T10:00:00"
  }
]
```

#### Get Issue Details

```http
GET /api/issues/{id}
Authorization: Bearer <token>
```

**Response**: `200 OK`
```json
{
  "id": "uuid",
  "file": "src/service/Auth.java",
  "line": 42,
  "severity": "HIGH",
  "category": "SECURITY",
  "description": "SQL injection vulnerability in login method",
  "suggestion": "Use PreparedStatement with parameterized queries",
  "codeSnippet": "String query = \"SELECT * FROM users WHERE id=\" + userId;",
  "resolved": false,
  "createdAt": "2024-01-15T10:00:00",
  "analysis": { ... },
  "branch": { ... }
}
```

### VCS Integration Endpoints

#### Connect Bitbucket

```http
POST /api/vcs/bitbucket/connect
Authorization: Bearer <token>
Content-Type: application/json

{
  "workspaceId": "workspace-uuid",
  "appPassword": "bitbucket-app-password",
  "username": "bitbucket-username"
}
```

**Response**: `201 Created`

#### List Repositories

```http
GET /api/vcs/bitbucket/repositories?workspaceId=uuid
Authorization: Bearer <token>
```

**Response**: `200 OK`
```json
[
  {
    "workspace": "my-workspace",
    "slug": "my-repo",
    "name": "My Repository",
    "url": "https://bitbucket.org/my-workspace/my-repo",
    "isPrivate": true,
    "mainBranch": "main"
  }
]
```

### AI Connection Endpoints

#### List AI Connections

```http
GET /api/ai/connections
Authorization: Bearer <token>
```

**Response**: `200 OK`
```json
[
  {
    "id": "uuid",
    "name": "OpenRouter",
    "provider": "OPENROUTER",
    "model": "anthropic/claude-3.5-sonnet",
    "createdAt": "2024-01-01T00:00:00"
  }
]
```

#### Create AI Connection

```http
POST /api/ai/connections
Authorization: Bearer <token>
Content-Type: application/json

{
  "name": "OpenRouter Production",
  "provider": "OPENROUTER",
  "apiKey": "sk-or-v1-...",
  "model": "anthropic/claude-3.5-sonnet",
  "configuration": {
    "temperature": 0.7,
    "maxTokens": 4096
  }
}
```

**Response**: `201 Created`

## Pipeline Agent API

### Webhook Endpoint

```http
POST /api/v1/bitbucket-cloud/webhook
Authorization: Bearer <project-token>
Content-Type: application/json

{
  // Bitbucket webhook payload
}
```

**Response**: `202 Accepted`
```json
{
  "status": "processing",
  "analysisId": "uuid"
}
```

## Response Codes

- `200 OK`: Request successful
- `201 Created`: Resource created
- `202 Accepted`: Request accepted, processing async
- `204 No Content`: Successful deletion
- `400 Bad Request`: Invalid request data
- `401 Unauthorized`: Missing or invalid authentication
- `403 Forbidden`: Insufficient permissions
- `404 Not Found`: Resource not found
- `409 Conflict`: Resource conflict (e.g., duplicate)
- `422 Unprocessable Entity`: Validation error
- `500 Internal Server Error`: Server error

## Error Response Format

```json
{
  "timestamp": "2024-01-15T10:00:00",
  "status": 400,
  "error": "Bad Request",
  "message": "Validation failed",
  "path": "/api/projects",
  "errors": [
    {
      "field": "name",
      "message": "Name is required"
    }
  ]
}
```

## Pagination

List endpoints support pagination:

**Query Parameters**:
- `page`: Page number (0-indexed)
- `size`: Items per page (default: 20, max: 100)
- `sort`: Sort field and direction (e.g., `createdAt,desc`)

**Response**:
```json
{
  "content": [...],
  "totalElements": 100,
  "totalPages": 5,
  "number": 0,
  "size": 20,
  "first": true,
  "last": false
}
```

## Rate Limiting

Currently not implemented. Consider adding for production:

- Per-user limits
- Per-project limits
- Webhook endpoint limits

## Swagger/OpenAPI

Interactive API documentation available at:

`http://localhost:8081/swagger-ui-custom.html`

OpenAPI spec:

`http://localhost:8081/api-docs`

