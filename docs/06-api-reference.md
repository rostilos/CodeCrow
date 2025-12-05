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

#### Get Branch Analysis Configuration

```http
GET /api/{workspaceSlug}/project/{namespace}/branch-analysis-config
Authorization: Bearer <token>
```

**Response**: `200 OK`
```json
{
  "prTargetBranches": ["main", "develop", "release/*"],
  "branchPushPatterns": ["main", "develop"]
}
```

Returns `null` if no configuration is set (all branches analyzed).

#### Update Branch Analysis Configuration

Configure which branches trigger automated analysis. Supports exact names and glob patterns.

```http
PUT /api/{workspaceSlug}/project/{namespace}/branch-analysis-config
Authorization: Bearer <token>
Content-Type: application/json

{
  "prTargetBranches": ["main", "develop", "release/*"],
  "branchPushPatterns": ["main", "develop"]
}
```

**Pattern Syntax**:
- `main` - Exact match
- `release/*` - Matches `release/1.0`, `release/2.0` (single level)
- `feature/**` - Matches `feature/auth`, `feature/auth/oauth` (any depth)

**Response**: `200 OK` - Returns updated ProjectDTO

**Default Behavior**: If arrays are empty or null, all branches are analyzed.

#### Get RAG Configuration

```http
GET /api/workspace/{workspaceSlug}/project/{projectNamespace}/rag/config
Authorization: Bearer <token>
```

**Response**: `200 OK`
```json
{
  "enabled": true,
  "branch": "main",
  "excludePatterns": ["vendor/**", "lib/**", "generated/**"]
}
```

#### Update RAG Configuration

Configure RAG indexing settings including exclude patterns.

```http
PUT /api/workspace/{workspaceSlug}/project/{projectNamespace}/rag/config
Authorization: Bearer <token>
Content-Type: application/json

{
  "enabled": true,
  "branch": "main",
  "excludePatterns": ["vendor/**", "lib/**", "app/design/**"]
}
```

**Fields**:
- `enabled` (required): Enable/disable RAG indexing
- `branch` (optional): Branch to index (null = use default branch)
- `excludePatterns` (optional): Array of glob patterns to exclude

**Exclude Pattern Syntax**:
- `vendor/**` - Directory and all subdirectories
- `*.min.js` - File extension pattern
- `**/*.generated.ts` - Pattern at any depth
- `lib/` - Directory prefix

**Response**: `200 OK` - Returns updated ProjectDTO

#### Get RAG Index Status

```http
GET /api/workspace/{workspaceSlug}/project/{projectNamespace}/rag/status
Authorization: Bearer <token>
```

**Response**: `200 OK`
```json
{
  "isIndexed": true,
  "indexStatus": {
    "projectId": 123,
    "status": "INDEXED",
    "indexedBranch": "main",
    "indexedCommitHash": "abc123def456",
    "totalFilesIndexed": 1250,
    "lastIndexedAt": "2024-01-15T10:30:00Z",
    "errorMessage": null,
    "collectionName": "codecrow_workspace__project__main"
  },
  "canStartIndexing": true
}
```

**Status Values**:
- `NOT_INDEXED` - Never indexed
- `INDEXING` - Currently indexing
- `INDEXED` - Successfully indexed
- `UPDATING` - Incremental update in progress
- `FAILED` - Last indexing failed

#### Trigger RAG Indexing

Manually trigger RAG indexing. Returns Server-Sent Events stream with progress updates.

```http
POST /api/workspace/{workspaceSlug}/project/{projectNamespace}/rag/trigger
Authorization: Bearer <token>
Accept: text/event-stream
```

**Response**: SSE Stream
```
event: message
data: {"type":"progress","stage":"init","message":"Starting RAG indexing..."}

event: message
data: {"type":"progress","stage":"download","message":"Downloading repository..."}

event: message
data: {"type":"progress","stage":"indexing","message":"Excluding 4 custom patterns"}

event: message
data: {"type":"complete","filesIndexed":1250}
```

**Rate Limiting**: Minimum 60 seconds between trigger requests per project.

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
POST /api/webhooks/{provider}/{authToken}
Content-Type: application/json

{
  // VCS provider webhook payload (Bitbucket, GitHub, GitLab)
}
```

**Response**: `202 Accepted`
```json
{
  "status": "accepted",
  "message": "Webhook received, processing started",
  "jobId": "uuid",
  "jobUrl": "https://codecrow.io/api/{workspace}/projects/{project}/jobs/{jobId}",
  "logsStreamUrl": "https://codecrow.io/api/jobs/{jobId}/logs/stream",
  "projectId": 123,
  "eventType": "pullrequest:created"
}
```

> **Note**: Webhooks now return immediately with a job ID. Use the `logsStreamUrl` for real-time progress via SSE, or poll the `jobUrl` for status updates.

## Jobs API

Background jobs track long-running operations like code analysis, RAG indexing, and repository sync. Jobs provide real-time progress tracking and persistent logs.

### Job Types

| Type | Description |
|------|-------------|
| `PR_ANALYSIS` | Pull request code review |
| `BRANCH_ANALYSIS` | Branch push analysis |
| `BRANCH_RECONCILIATION` | Post-merge reconciliation |
| `RAG_INITIAL_INDEX` | Initial repository indexing |
| `RAG_INCREMENTAL_INDEX` | Incremental index update |
| `MANUAL_ANALYSIS` | On-demand analysis |
| `REPO_SYNC` | Repository synchronization |

### Job Statuses

| Status | Description |
|--------|-------------|
| `PENDING` | Job created, not yet started |
| `QUEUED` | Waiting for resources |
| `RUNNING` | Currently executing |
| `COMPLETED` | Finished successfully |
| `FAILED` | Finished with error |
| `CANCELLED` | Cancelled by user/system |
| `WAITING` | Waiting for lock release |

### List Workspace Jobs

```http
GET /api/{workspaceSlug}/jobs
Authorization: Bearer <token>
```

**Query Parameters**:
- `page`: Page number (0-indexed)
- `size`: Items per page (default: 20)
- `status`: Filter by status (RUNNING, COMPLETED, FAILED, etc.)
- `type`: Filter by job type (PR_ANALYSIS, BRANCH_ANALYSIS, etc.)

**Response**: `200 OK`
```json
{
  "jobs": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "projectId": 123,
      "projectName": "My Project",
      "projectNamespace": "my-project",
      "workspaceId": 1,
      "workspaceName": "My Workspace",
      "jobType": "PR_ANALYSIS",
      "status": "COMPLETED",
      "triggerSource": "WEBHOOK",
      "title": "PR #42 Analysis: feature/auth → main",
      "branchName": "main",
      "prNumber": 42,
      "commitHash": "abc1234",
      "progress": 100,
      "currentStep": "complete",
      "createdAt": "2024-01-15T10:00:00Z",
      "startedAt": "2024-01-15T10:00:01Z",
      "completedAt": "2024-01-15T10:02:30Z",
      "durationMs": 149000,
      "logCount": 45
    }
  ],
  "page": 0,
  "pageSize": 20,
  "totalElements": 150,
  "totalPages": 8
}
```

### List Project Jobs

```http
GET /api/{workspaceSlug}/projects/{projectNamespace}/jobs
Authorization: Bearer <token>
```

**Query Parameters**: Same as workspace jobs

**Response**: Same format as workspace jobs

### Get Active Jobs

```http
GET /api/{workspaceSlug}/projects/{projectNamespace}/jobs/active
Authorization: Bearer <token>
```

**Response**: `200 OK`
```json
[
  {
    "id": "uuid",
    "jobType": "PR_ANALYSIS",
    "status": "RUNNING",
    "progress": 65,
    "currentStep": "analyzing_diff",
    ...
  }
]
```

### Get Job Details

```http
GET /api/{workspaceSlug}/projects/{projectNamespace}/jobs/{jobId}
Authorization: Bearer <token>
```

**Response**: `200 OK`
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "projectId": 123,
  "projectName": "My Project",
  "jobType": "PR_ANALYSIS",
  "status": "RUNNING",
  "triggerSource": "WEBHOOK",
  "title": "PR #42 Analysis",
  "branchName": "main",
  "prNumber": 42,
  "commitHash": "abc1234def5678",
  "progress": 75,
  "currentStep": "generating_report",
  "createdAt": "2024-01-15T10:00:00Z",
  "startedAt": "2024-01-15T10:00:01Z",
  "durationMs": 45000,
  "logCount": 32
}
```

### Get Job Logs

```http
GET /api/{workspaceSlug}/projects/{projectNamespace}/jobs/{jobId}/logs
Authorization: Bearer <token>
```

**Query Parameters**:
- `afterSequence`: Return logs after this sequence number (for pagination/polling)

**Response**: `200 OK`
```json
{
  "jobId": "550e8400-e29b-41d4-a716-446655440000",
  "logs": [
    {
      "id": "log-uuid-1",
      "sequenceNumber": 1,
      "level": "INFO",
      "step": "init",
      "message": "Job created for PR #42",
      "timestamp": "2024-01-15T10:00:00Z"
    },
    {
      "id": "log-uuid-2",
      "sequenceNumber": 2,
      "level": "INFO",
      "step": "fetching_diff",
      "message": "Fetching PR diff from Bitbucket",
      "timestamp": "2024-01-15T10:00:01Z"
    },
    {
      "id": "log-uuid-3",
      "sequenceNumber": 3,
      "level": "WARN",
      "step": "analysis",
      "message": "Large file detected, truncating context",
      "metadata": "{\"file\": \"large-bundle.js\", \"size\": 5242880}",
      "timestamp": "2024-01-15T10:00:15Z"
    }
  ],
  "latestSequence": 32,
  "isComplete": false
}
```

### Stream Job Logs (SSE)

Real-time log streaming using Server-Sent Events.

```http
GET /api/{workspaceSlug}/projects/{projectNamespace}/jobs/{jobId}/logs/stream
Authorization: Bearer <token>
Accept: text/event-stream
```

**Query Parameters**:
- `afterSequence`: Start streaming from this sequence (default: 0)

**SSE Events**:

```
event: log
data: {"id":"uuid","sequenceNumber":1,"level":"INFO","step":"init","message":"Job started","timestamp":"2024-01-15T10:00:00Z"}

event: log
data: {"id":"uuid","sequenceNumber":2,"level":"INFO","step":"fetching_diff","message":"Fetching diff...","timestamp":"2024-01-15T10:00:01Z"}

event: complete
data: {"status":"COMPLETED","message":"Job completed successfully"}
```

**JavaScript Example**:
```javascript
const eventSource = new EventSource(
  '/api/workspace/projects/my-project/jobs/job-uuid/logs/stream?afterSequence=0'
);

eventSource.addEventListener('log', (event) => {
  const log = JSON.parse(event.data);
  console.log(`[${log.level}] ${log.message}`);
});

eventSource.addEventListener('complete', (event) => {
  const { status, message } = JSON.parse(event.data);
  console.log(`Job ${status}: ${message}`);
  eventSource.close();
});

eventSource.onerror = () => {
  eventSource.close();
};
```

### Cancel Job

```http
POST /api/{workspaceSlug}/projects/{projectNamespace}/jobs/{jobId}/cancel
Authorization: Bearer <token>
```

**Response**: `200 OK`
```json
{
  "id": "uuid",
  "status": "CANCELLED",
  ...
}
```

### Public Job Endpoints

These endpoints use the job's external UUID directly, useful for webhook responses.

#### Get Job by External ID

```http
GET /api/jobs/{jobId}
```

#### Stream Logs by External ID

```http
GET /api/jobs/{jobId}/logs/stream
Accept: text/event-stream
```
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

