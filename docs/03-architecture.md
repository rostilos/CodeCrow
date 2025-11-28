# Architecture

## System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                         Bitbucket Cloud                         │
└────────────────────────────┬────────────────────────────────────┘
                             │ Webhooks
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Pipeline Agent (8082)                      │
│  ┌────────────────────────────────────────────────────────┐    │
│  │ Webhook Controller                                      │    │
│  │  - Validates requests                                   │    │
│  │  - Acquires analysis lock                               │    │
│  │  - Fetches repository data via VCS client               │    │
│  └──────────────┬──────────────────────────────────────────┘    │
│                 │                                                │
│  ┌──────────────▼──────────────────────────────────────────┐    │
│  │ Analysis Service                                         │    │
│  │  - Prepares analysis request                             │    │
│  │  - Checks for previous issues                            │    │
│  │  - Sends to MCP Client                                   │    │
│  └──────────────┬──────────────────────────────────────────┘    │
│                 │                                                │
│  ┌──────────────▼──────────────────────────────────────────┐    │
│  │ RAG Integration Service                                  │    │
│  │  - Triggers indexing for first branch analysis           │    │
│  │  - Updates index incrementally                           │    │
│  └──────────────┬──────────────────────────────────────────┘    │
│                 │                                                │
│  ┌──────────────▼──────────────────────────────────────────┐    │
│  │ Result Processor                                         │    │
│  │  - Processes analysis results                            │    │
│  │  - Updates issue statuses                                │    │
│  │  - Stores in database                                    │    │
│  └──────────────────────────────────────────────────────────┘    │
└───────┬─────────────────────────────────┬───────────────────────┘
        │                                 │
        │                                 │ RAG requests
        ▼                                 ▼
┌──────────────────┐           ┌──────────────────────────┐
│  MCP Client      │           │    RAG Pipeline          │
│    (8000)        │◄──────────┤      (8001)              │
│                  │  Context  │                          │
│ ┌──────────────┐ │           │ ┌──────────────────────┐ │
│ │ Prompt Gen   │ │           │ │ Indexing Service     │ │
│ │  - Builds    │ │           │ │  - Full index build  │ │
│ │    prompts   │ │           │ │  - Incremental upd   │ │
│ │  - RAG query │ │           │ └──────────────────────┘ │
│ └──────┬───────┘ │           │                          │
│        │         │           │ ┌──────────────────────┐ │
│ ┌──────▼───────┐ │           │ │ Query Service        │ │
│ │ MCP Tools    │ │           │ │  - Semantic search   │ │
│ │  - Bitbucket │ │           │ │  - Context retrieval │ │
│ │    MCP       │ │           │ └──────────────────────┘ │
│ └──────┬───────┘ │           │                          │
│        │         │           │ ┌──────────────────────┐ │
│ ┌──────▼───────┐ │           │ │ Qdrant Integration   │ │
│ │ LLM Client   │ │           │ │  - Vector operations │ │
│ │  - OpenRouter│ │           │ └──────────────────────┘ │
│ └──────────────┘ │           └──────────────────────────┘
└──────────────────┘
        │
        │ API calls
        ▼
┌──────────────────────────────┐
│   OpenRouter / LLM Provider  │
└──────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                      Web Server (8081)                          │
│  ┌────────────────────────────────────────────────────────┐    │
│  │ REST API Controllers                                    │    │
│  │  - Auth, Users, Workspaces, Projects                    │    │
│  │  - Analysis, Issues, Pull Requests                      │    │
│  │  - VCS Integration, AI Connections                      │    │
│  └──────────────┬──────────────────────────────────────────┘    │
│                 │                                                │
│  ┌──────────────▼──────────────────────────────────────────┐    │
│  │ Business Logic Services                                  │    │
│  └──────────────┬──────────────────────────────────────────┘    │
│                 │                                                │
│  ┌──────────────▼──────────────────────────────────────────┐    │
│  │ Security Layer (JWT, Permissions)                        │    │
│  └──────────────────────────────────────────────────────────┘    │
└───────┬─────────────────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────┐
│      PostgreSQL (5432)        │
│  ┌─────────────────────────┐ │
│  │ - Users, Workspaces     │ │
│  │ - Projects, Branches    │ │
│  │ - Issues, Analyses      │ │
│  │ - Permissions, Tokens   │ │
│  └─────────────────────────┘ │
└──────────────────────────────┘

┌──────────────────────────────┐
│       Redis (6379)           │
│  - Sessions                  │
│  - Cache                     │
└──────────────────────────────┘

┌──────────────────────────────┐
│      Qdrant (6333)           │
│  - Code embeddings           │
│  - Semantic search           │
└──────────────────────────────┘

        ▲
        │
┌───────┴──────────────────────┐
│   Frontend (8080)            │
│  - React SPA                 │
│  - User interface            │
└──────────────────────────────┘
```

## Component Interactions

### 1. Webhook Processing Flow

```
Bitbucket → Pipeline Agent → Lock Check → Fetch Code → 
Build Request → MCP Client → AI Analysis → Store Results
```

1. Bitbucket sends webhook (PR event or push event)
2. Pipeline agent validates webhook signature and project token
3. Acquires analysis lock to prevent concurrent analysis
4. VCS client fetches repository metadata, diffs, file content
5. For first branch analysis, triggers RAG indexing
6. Builds analysis request with context
7. Sends to MCP client
8. MCP client queries RAG for relevant code context
9. Generates prompts with context
10. Calls OpenRouter LLM
11. Returns analysis results
12. Pipeline agent processes results, updates database
13. Releases analysis lock

### 2. Branch Analysis Flow

```
PR Merged → Webhook → Pipeline Agent → Fetch Changed Files → 
Query Existing Issues → RAG Context → MCP Analysis → 
Update Issue Status → Store Results
```

**First Branch Analysis**:
- Triggers full repository indexing in RAG
- Creates RAG index status entry
- All repository files indexed for semantic search

**Subsequent Branch Analysis**:
- Incremental RAG index update
- Only changed files re-indexed
- Existing branch issues checked for resolution
- Issues marked as resolved if fixed in merge

### 3. Pull Request Analysis Flow

```
PR Created/Updated → Webhook → Pipeline Agent → Fetch Diffs → 
Query Previous PR Issues → RAG Context → MCP Analysis → 
Store Issues → Link to PR
```

- Analyzes only changed files (diff)
- Reuses previous analysis if PR was analyzed before
- Creates new CodeAnalysisIssue entries
- Links issues to CodeAnalysis and PullRequest entities

### 4. RAG Integration Flow

```
Code Files → Chunking → Embedding (OpenRouter) → 
Qdrant Storage → Semantic Search → Context Retrieval
```

**Indexing**:
- Files split into chunks (800 chars for code, 1000 for text)
- Chunks embedded using OpenRouter embedding model
- Vectors stored in Qdrant with metadata

**Retrieval**:
- Query embedded using same model
- Similarity search in Qdrant
- Top K results returned with context

### 5. Authentication Flow

```
User Login → JWT Token → Store in Session → 
Validate on Each Request → Permission Check
```

- JWT tokens for API authentication
- Redis for session storage
- Role-based access control (workspace and project level)
- Project tokens for webhook authentication

## Data Flow

### Analysis Request Structure

```json
{
  "project_id": "uuid",
  "analysis_type": "PULL_REQUEST | BRANCH",
  "repository": {
    "workspace": "workspace-slug",
    "repo_slug": "repo-slug",
    "branch": "feature/branch-name"
  },
  "changed_files": [
    {
      "path": "src/main/java/Example.java",
      "diff": "...",
      "content": "..."
    }
  ],
  "previous_issues": [...],
  "metadata": {
    "pr_number": 123,
    "author": "username"
  }
}
```

### Analysis Response Structure

```json
{
  "issues": [
    {
      "file": "src/main/java/Example.java",
      "line": 42,
      "severity": "HIGH",
      "category": "SECURITY",
      "description": "SQL injection vulnerability",
      "suggestion": "Use parameterized queries"
    }
  ],
  "summary": {
    "total_issues": 5,
    "by_severity": {"HIGH": 1, "MEDIUM": 3, "LOW": 1}
  }
}
```

## Scalability Considerations

### Horizontal Scaling
- **Web Server**: Stateless, can scale horizontally behind load balancer
- **Pipeline Agent**: Single instance recommended (uses DB locks for concurrency)
- **MCP Client**: Can scale with queue-based distribution
- **RAG Pipeline**: Can scale for read operations

### Performance Optimization
- Analysis locks prevent concurrent analysis of same repository
- Redis caching for frequently accessed data
- Incremental RAG updates reduce indexing overhead
- Async processing for long-running analyses

### Resource Requirements

**Minimum**:
- 4 CPU cores
- 8GB RAM
- 50GB disk

**Recommended**:
- 8 CPU cores
- 16GB RAM
- 200GB SSD

**Database**:
- PostgreSQL with regular vacuuming
- Indexes on frequently queried columns

**Vector Database**:
- Qdrant memory mapped mode for large codebases
- SSD for better performance

## Security Architecture

### Authentication Layers
1. **User Authentication**: JWT tokens, session management
2. **Webhook Authentication**: Project-specific tokens
3. **Inter-Service**: Internal network, no public exposure

### Authorization
- Workspace-level roles (Owner, Admin, Member, Viewer)
- Project-level permissions
- Permission templates for flexible access control

### Data Protection
- Sensitive data encrypted at rest (encryption key in config)
- JWT secrets for token signing
- HTTPS recommended for production
- VCS credentials encrypted in database

## Technology Decisions

### Why Java + Spring Boot?
- Robust enterprise framework
- Strong typing and compile-time safety
- Excellent ORM (Hibernate/JPA)
- Large ecosystem

### Why Python for MCP Client?
- MCP SDK available in Python
- LangChain/LlamaIndex for RAG
- Rapid development for AI integration
- Rich AI/ML library ecosystem

### Why Qdrant?
- Fast vector similarity search
- Easy Docker deployment
- Good Python/REST API support
- Memory-efficient

### Why OpenRouter?
- Single API for multiple LLM providers
- No vendor lock-in
- Cost-effective
- Easy to switch models

