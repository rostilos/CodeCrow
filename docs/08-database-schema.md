# Database Schema

CodeCrow uses PostgreSQL as the primary relational database.

## Entity Relationship Diagram

```
┌──────────────┐
│     User     │
│──────────────│
│ id (PK)      │
│ username     │
│ email        │
│ password     │
│ roles        │
│ created_at   │
└──────┬───────┘
       │
       │ 1:N
       │
┌──────▼──────────────┐         ┌─────────────────┐
│ WorkspaceMember     │    N:1  │   Workspace     │
│─────────────────────│◄────────┤─────────────────│
│ id (PK)             │         │ id (PK)         │
│ workspace_id (FK)   │         │ name            │
│ user_id (FK)        │         │ description     │
│ role                │         │ created_at      │
│ joined_at           │         └────────┬────────┘
└─────────────────────┘                  │
                                         │ 1:N
                                         │
                            ┌────────────▼────────────┐
                            │      Project            │
                            │─────────────────────────│
                            │ id (PK)                 │
                            │ workspace_id (FK)       │
                            │ name                    │
                            │ description             │
                            │ repository_url          │
                            │ default_branch          │
                            │ created_at              │
                            └────────┬────────────────┘
                                     │
                ┌────────────────────┼────────────────────┐
                │                    │                    │
                │ 1:N                │ 1:N                │ 1:N
         ┌──────▼────────┐    ┌─────▼─────┐      ┌──────▼────────┐
         │    Branch     │    │ProjectToken│      │  CodeAnalysis │
         │───────────────│    │────────────│      │───────────────│
         │ id (PK)       │    │ id (PK)    │      │ id (PK)       │
         │ project_id(FK)│    │ project_id │      │ project_id(FK)│
         │ name          │    │ token      │      │ pr_id (FK)    │
         │ created_at    │    │ name       │      │ status        │
         └───┬───────────┘    │ expires_at │      │ created_at    │
             │                └────────────┘      │ completed_at  │
             │ 1:N                                └───┬───────────┘
             │                                        │
    ┌────────┼──────────┐                            │ 1:N
    │        │          │                             │
    │ 1:N    │ 1:N      │ 1:1                 ┌───────▼──────────────┐
┌───▼──────┐ │  ┌───────▼────────┐    ┌───────┤ CodeAnalysisIssue  │
│BranchFile│ │  │  BranchIssue   │    │       │────────────────────│
│──────────│ │  │────────────────│    │       │ id (PK)            │
│ id (PK)  │ │  │ id (PK)        │    │       │ analysis_id (FK)   │
│branch(FK)│ │  │ branch_id (FK) │    │       │ file               │
│ path     │ │  │ file           │    │       │ line               │
│ hash     │ │  │ line           │    │       │ severity           │
└──────────┘ │  │ severity       │    │       │ category           │
             │  │ category       │    │       │ description        │
             │  │ description    │    │       │ suggestion         │
             │  │ resolved       │    │       │ code_snippet       │
             │  │ resolved_at    │    │       └────────────────────┘
             │  │ created_at     │    │
             │  └────────────────┘    │
             │                        │
             │ 1:1                    │
        ┌────▼───────────┐    ┌───────▼──────┐
        │RagIndexStatus  │    │ PullRequest  │
        │────────────────│    │──────────────│
        │ id (PK)        │    │ id (PK)      │
        │ branch_id (FK) │    │ project_id   │
        │ status         │    │ number       │
        │ started_at     │    │ title        │
        │ completed_at   │    │ author       │
        │ error_message  │    │ source_branch│
        └────────────────┘    │ target_branch│
                              │ created_at   │
                              └──────────────┘

┌──────────────────┐        ┌─────────────────────────┐
│  AIConnection    │        │ ProjectVcsConnectionBinding│
│──────────────────│        │─────────────────────────│
│ id (PK)          │        │ id (PK)                 │
│ name             │        │ project_id (FK)         │
│ provider         │        │ vcs_type                │
│ api_key (enc)    │        │ username                │
│ model            │        │ app_password (enc)      │
│ configuration    │        │ workspace_slug          │
└──────────────────┘        └─────────────────────────┘

┌───────────────────┐
│  AnalysisLock     │
│───────────────────│
│ id (PK)           │
│ repository        │
│ branch            │
│ locked_at         │
│ locked_by         │
└───────────────────┘
```

## Tables

### User

User accounts and authentication.

```sql
CREATE TABLE user (
  id UUID PRIMARY KEY,
  username VARCHAR(255) UNIQUE NOT NULL,
  email VARCHAR(255) UNIQUE NOT NULL,
  password VARCHAR(255) NOT NULL,
  roles VARCHAR(255)[] DEFAULT ARRAY['USER'],
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_user_username ON user(username);
CREATE INDEX idx_user_email ON user(email);
```

**Fields**:
- `id`: Unique user identifier (UUID)
- `username`: Unique username
- `email`: Unique email address
- `password`: BCrypt hashed password
- `roles`: Array of roles (USER, ADMIN)
- `created_at`: Account creation timestamp
- `updated_at`: Last update timestamp

### Workspace

Top-level organizational units.

```sql
CREATE TABLE workspace (
  id UUID PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  description TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_workspace_name ON workspace(name);
```

### WorkspaceMember

Workspace membership and roles.

```sql
CREATE TABLE workspace_member (
  id UUID PRIMARY KEY,
  workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES user(id) ON DELETE CASCADE,
  role VARCHAR(50) NOT NULL,
  joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(workspace_id, user_id)
);

CREATE INDEX idx_workspace_member_workspace ON workspace_member(workspace_id);
CREATE INDEX idx_workspace_member_user ON workspace_member(user_id);
```

**Roles**: OWNER, ADMIN, MEMBER, VIEWER

### Project

Repository projects within workspaces.

```sql
CREATE TABLE project (
  id UUID PRIMARY KEY,
  workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
  name VARCHAR(255) NOT NULL,
  description TEXT,
  repository_url VARCHAR(500) NOT NULL,
  default_branch VARCHAR(255) DEFAULT 'main',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_project_workspace ON project(workspace_id);
CREATE INDEX idx_project_name ON project(name);
```

### ProjectToken

Webhook authentication tokens.

```sql
CREATE TABLE project_token (
  id UUID PRIMARY KEY,
  project_id UUID NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  token VARCHAR(500) UNIQUE NOT NULL,
  name VARCHAR(255),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  expires_at TIMESTAMP
);

CREATE INDEX idx_project_token_project ON project_token(project_id);
CREATE INDEX idx_project_token_token ON project_token(token);
```

### Branch

Git branches being tracked.

```sql
CREATE TABLE branch (
  id UUID PRIMARY KEY,
  project_id UUID NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  name VARCHAR(255) NOT NULL,
  last_commit_hash VARCHAR(255),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(project_id, name)
);

CREATE INDEX idx_branch_project ON branch(project_id);
CREATE INDEX idx_branch_name ON branch(name);
```

### BranchFile

Files tracked in branches.

```sql
CREATE TABLE branch_file (
  id UUID PRIMARY KEY,
  branch_id UUID NOT NULL REFERENCES branch(id) ON DELETE CASCADE,
  path VARCHAR(1000) NOT NULL,
  file_hash VARCHAR(255),
  last_modified TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(branch_id, path)
);

CREATE INDEX idx_branch_file_branch ON branch_file(branch_id);
CREATE INDEX idx_branch_file_path ON branch_file(path);
```

### BranchIssue

Issues found in branch analysis.

```sql
CREATE TABLE branch_issue (
  id UUID PRIMARY KEY,
  branch_id UUID NOT NULL REFERENCES branch(id) ON DELETE CASCADE,
  file VARCHAR(1000) NOT NULL,
  line INTEGER,
  severity VARCHAR(50) NOT NULL,
  category VARCHAR(100) NOT NULL,
  description TEXT NOT NULL,
  suggestion TEXT,
  code_snippet TEXT,
  resolved BOOLEAN DEFAULT FALSE,
  resolved_at TIMESTAMP,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_branch_issue_branch ON branch_issue(branch_id);
CREATE INDEX idx_branch_issue_resolved ON branch_issue(resolved);
CREATE INDEX idx_branch_issue_severity ON branch_issue(severity);
CREATE INDEX idx_branch_issue_file ON branch_issue(file);
```

### RagIndexStatus

RAG indexing status per branch.

```sql
CREATE TABLE rag_index_status (
  id UUID PRIMARY KEY,
  branch_id UUID UNIQUE NOT NULL REFERENCES branch(id) ON DELETE CASCADE,
  status VARCHAR(50) NOT NULL,
  started_at TIMESTAMP,
  completed_at TIMESTAMP,
  error_message TEXT,
  total_files INTEGER,
  indexed_files INTEGER
);

CREATE INDEX idx_rag_index_branch ON rag_index_status(branch_id);
CREATE INDEX idx_rag_index_status ON rag_index_status(status);
```

**Status Values**: PENDING, IN_PROGRESS, COMPLETED, FAILED

### PullRequest

Pull request metadata.

```sql
CREATE TABLE pull_request (
  id UUID PRIMARY KEY,
  project_id UUID NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  number INTEGER NOT NULL,
  title VARCHAR(500),
  description TEXT,
  author VARCHAR(255),
  source_branch VARCHAR(255) NOT NULL,
  target_branch VARCHAR(255) NOT NULL,
  status VARCHAR(50) NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(project_id, number)
);

CREATE INDEX idx_pull_request_project ON pull_request(project_id);
CREATE INDEX idx_pull_request_number ON pull_request(project_id, number);
CREATE INDEX idx_pull_request_status ON pull_request(status);
```

**Status Values**: OPEN, MERGED, DECLINED

### CodeAnalysis

Pull request analysis records.

```sql
CREATE TABLE code_analysis (
  id UUID PRIMARY KEY,
  project_id UUID NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  pull_request_id UUID REFERENCES pull_request(id) ON DELETE CASCADE,
  status VARCHAR(50) NOT NULL,
  total_issues INTEGER DEFAULT 0,
  high_severity INTEGER DEFAULT 0,
  medium_severity INTEGER DEFAULT 0,
  low_severity INTEGER DEFAULT 0,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  completed_at TIMESTAMP
);

CREATE INDEX idx_code_analysis_project ON code_analysis(project_id);
CREATE INDEX idx_code_analysis_pr ON code_analysis(pull_request_id);
CREATE INDEX idx_code_analysis_status ON code_analysis(status);
CREATE INDEX idx_code_analysis_created ON code_analysis(created_at DESC);
```

**Status Values**: PENDING, IN_PROGRESS, COMPLETED, FAILED

### CodeAnalysisIssue

Issues found in PR analysis.

```sql
CREATE TABLE code_analysis_issue (
  id UUID PRIMARY KEY,
  analysis_id UUID NOT NULL REFERENCES code_analysis(id) ON DELETE CASCADE,
  file VARCHAR(1000) NOT NULL,
  line INTEGER,
  severity VARCHAR(50) NOT NULL,
  category VARCHAR(100) NOT NULL,
  description TEXT NOT NULL,
  suggestion TEXT,
  code_snippet TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_code_analysis_issue_analysis ON code_analysis_issue(analysis_id);
CREATE INDEX idx_code_analysis_issue_severity ON code_analysis_issue(severity);
CREATE INDEX idx_code_analysis_issue_category ON code_analysis_issue(category);
CREATE INDEX idx_code_analysis_issue_file ON code_analysis_issue(file);
```

### AIConnection

AI provider configurations.

```sql
CREATE TABLE ai_connection (
  id UUID PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  provider VARCHAR(100) NOT NULL,
  api_key VARCHAR(1000) NOT NULL,
  model VARCHAR(255),
  configuration JSONB,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_ai_connection_provider ON ai_connection(provider);
```

**api_key**: Encrypted with AES

### ProjectVcsConnectionBinding

VCS connection per project.

```sql
CREATE TABLE project_vcs_connection_binding (
  id UUID PRIMARY KEY,
  project_id UUID UNIQUE NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  vcs_type VARCHAR(50) NOT NULL,
  username VARCHAR(255),
  app_password VARCHAR(1000),
  workspace_slug VARCHAR(255),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_vcs_binding_project ON project_vcs_connection_binding(project_id);
```

**app_password**: Encrypted with AES

### AnalysisLock

Analysis locking mechanism.

```sql
CREATE TABLE analysis_lock (
  id UUID PRIMARY KEY,
  repository VARCHAR(500) NOT NULL,
  branch VARCHAR(255) NOT NULL,
  locked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  locked_by VARCHAR(255),
  UNIQUE(repository, branch)
);

CREATE INDEX idx_analysis_lock_repo_branch ON analysis_lock(repository, branch);
CREATE INDEX idx_analysis_lock_timestamp ON analysis_lock(locked_at);
```

## Data Encryption

Sensitive fields are encrypted using AES-256:

- `ai_connection.api_key`
- `project_vcs_connection_binding.app_password`

Encryption key configured in `application.properties`:
```properties
codecrow.security.encryption-key=<base64-key>
```

## Database Migrations

Currently using Hibernate auto-DDL:
```properties
spring.jpa.hibernate.ddl-auto=update
```

For production, consider:
- Flyway for versioned migrations
- Liquibase for database-agnostic migrations
- Manual migrations with version control

## Backup Strategy

### Full Backup

```bash
docker exec codecrow-postgres pg_dump -U codecrow_user codecrow_ai > backup.sql
```

### Restore

```bash
cat backup.sql | docker exec -i codecrow-postgres psql -U codecrow_user -d codecrow_ai
```

### Automated Backups

Setup cron job for daily backups:
```bash
0 2 * * * docker exec codecrow-postgres pg_dump -U codecrow_user codecrow_ai | gzip > /backups/codecrow_$(date +\%Y\%m\%d).sql.gz
```

## Performance Tuning

### Index Optimization

Key indexes already defined. Monitor query performance and add as needed:

```sql
-- Example: Add composite index
CREATE INDEX idx_branch_issue_branch_resolved ON branch_issue(branch_id, resolved);

-- Example: Partial index for active issues
CREATE INDEX idx_branch_issue_active ON branch_issue(branch_id) WHERE resolved = FALSE;
```

### Query Optimization

Use EXPLAIN to analyze slow queries:
```sql
EXPLAIN ANALYZE SELECT * FROM branch_issue WHERE branch_id = '...' AND resolved = FALSE;
```

### Connection Pooling

Configure HikariCP (default in Spring Boot):
```properties
spring.datasource.hikari.maximum-pool-size=10
spring.datasource.hikari.minimum-idle=5
spring.datasource.hikari.connection-timeout=30000
```

### Maintenance

Regular maintenance tasks:
```sql
-- Vacuum to reclaim storage
VACUUM ANALYZE branch_issue;

-- Reindex for better performance
REINDEX TABLE branch_issue;

-- Update statistics
ANALYZE branch_issue;
```

## Data Retention

Consider implementing data retention policies:

```sql
-- Archive old resolved issues
DELETE FROM branch_issue 
WHERE resolved = TRUE 
AND resolved_at < NOW() - INTERVAL '1 year';

-- Archive old analyses
DELETE FROM code_analysis 
WHERE completed_at < NOW() - INTERVAL '6 months' 
AND status = 'COMPLETED';
```

## Monitoring

Monitor database health:

```sql
-- Check database size
SELECT pg_size_pretty(pg_database_size('codecrow_ai'));

-- Check table sizes
SELECT schemaname, tablename, 
       pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) 
FROM pg_tables 
WHERE schemaname = 'public' 
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;

-- Check active connections
SELECT count(*) FROM pg_stat_activity WHERE datname = 'codecrow_ai';
```

