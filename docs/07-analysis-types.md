# Analysis Types

CodeCrow performs two types of automated code analysis: Branch Analysis and Pull Request Analysis.

## Branch Analysis

### Overview

Branch analysis is triggered when a pull request is merged into a branch. It performs incremental analysis of the target branch to verify if previously reported issues have been resolved.

### Trigger Event

**Bitbucket Webhook**: `repo:push` event

**Conditions**:
- Push is a merge (pull request merged)
- Target branch is tracked by CodeCrow
- Project has active webhook token

### Flow Diagram

```
PR Merged → Push Event → Webhook Received → 
Acquire Lock → Fetch Changed Files → 
Check First Analysis → RAG Indexing/Update →
Query Existing Issues → Build Request → 
MCP Client Analysis → Process Results → 
Update Issue Status → Release Lock
```

### First Branch Analysis

When a branch is analyzed for the first time:

1. **Full Repository Indexing**:
   - All repository files are fetched
   - RAG pipeline indexes entire codebase
   - Vector embeddings stored in Qdrant
   - RagIndexStatus record created with status `COMPLETED`

2. **No Existing Issues**:
   - No previous issues to check
   - Analysis focuses on new code quality

3. **Baseline Established**:
   - All detected issues stored as BranchIssue
   - Provides baseline for future analyses

### Subsequent Branch Analysis

For branches with existing analyses:

1. **Incremental RAG Update**:
   - Only changed files re-indexed
   - Existing embeddings updated or removed
   - New file embeddings added
   - RagIndexStatus updated

2. **Issue Resolution Check**:
   - Fetch all active BranchIssue records for branch
   - Filter issues related to changed files
   - Include in analysis request as `previous_issues`

3. **AI Re-Analysis**:
   - LLM checks if issues are resolved
   - Compares previous issue location with new code
   - Returns resolution status for each issue

4. **Issue Status Update**:
   - Issues marked as resolved: `resolved = true`
   - Unresolved issues remain active
   - New issues added as BranchIssue

### Request Structure

```json
{
  "project_id": "uuid",
  "analysis_type": "BRANCH",
  "repository": {
    "workspace": "my-workspace",
    "repo_slug": "my-repo",
    "branch": "main"
  },
  "changed_files": [
    {
      "path": "src/service/auth.java",
      "diff": "@@ -10,5 +10,7 @@...",
      "content": "package org.example..."
    }
  ],
  "previous_issues": [
    {
      "id": "issue-uuid",
      "file": "src/service/auth.java",
      "line": 42,
      "severity": "HIGH",
      "description": "SQL injection vulnerability"
    }
  ],
  "metadata": {
    "merge_commit": "abc123",
    "pr_number": 45,
    "author": "username"
  }
}
```

### Response Processing

**Resolution Detected**:
```json
{
  "resolved_issues": [
    {
      "issue_id": "issue-uuid",
      "resolved": true,
      "reason": "Parameterized queries now used"
    }
  ],
  "new_issues": [...]
}
```

**Update Database**:
- Set `BranchIssue.resolved = true` for resolved issues
- Set `BranchIssue.resolved_at = timestamp`
- Create new BranchIssue records for new issues

### RAG Integration

**First Analysis**:
```python
# Full index build
POST /index
{
  "project_id": "proj-123",
  "repository": "my-repo",
  "branch": "main",
  "files": [...all repository files...],
  "incremental": false
}
```

**Incremental Update**:
```python
# Update only changed files
POST /index
{
  "project_id": "proj-123",
  "repository": "my-repo",
  "branch": "main",
  "files": [...changed files only...],
  "incremental": true
}
```

### Locking Mechanism

**Purpose**: Prevent concurrent branch analysis

**Implementation**:
- Database record in `AnalysisLock` table
- Lock acquired before analysis starts
- Lock includes: repository, branch, timestamp
- Lock released after analysis completes
- Stale locks auto-expire (configurable timeout)

**Lock Check**:
```sql
SELECT * FROM analysis_lock 
WHERE repository = ? AND branch = ? 
AND locked_at > NOW() - INTERVAL '30 minutes';
```

If lock exists and not expired, analysis is skipped.

### Performance Considerations

**Large Repositories**:
- First analysis can take 10-30 minutes for large codebases
- Incremental updates typically < 1 minute
- RAG indexing is the bottleneck

**Optimization**:
- Index only supported file types
- Skip binary files and build artifacts
- Use file size limits
- Batch embedding generation

### Error Handling

**RAG Indexing Failed**:
- RagIndexStatus marked as `FAILED`
- Branch analysis continues without RAG context
- Retry indexing on next analysis

**MCP Client Timeout**:
- Analysis marked as `FAILED`
- Lock released
- Webhook can retry

**Partial Success**:
- Some issues updated, others failed
- Record error details in logs
- Return partial results

## Pull Request Analysis

### Overview

Pull request analysis is triggered when a PR is created or updated. It analyzes only the changed code in the PR.

### Trigger Events

**Bitbucket Webhooks**:
- `pullrequest:created`
- `pullrequest:updated`

**Conditions**:
- PR is in open state
- Target branch belongs to tracked project
- Project has active webhook token

### Flow Diagram

```
PR Created/Updated → Webhook Received → 
Acquire Lock → Fetch PR Metadata → 
Fetch Diffs → Check Previous PR Analysis →
Query RAG Context → Build Request → 
MCP Client Analysis → Process Results → 
Create/Update CodeAnalysis → Create Issues → 
Link to PR → Release Lock
```

### First PR Analysis

When a PR is analyzed for the first time:

1. **Fetch PR Data**:
   - PR metadata (author, title, description, source/target branches)
   - Diffs for all changed files
   - File content for context

2. **RAG Context**:
   - Query RAG for relevant code from target branch
   - Provides context about related code
   - Helps AI understand broader codebase

3. **Analysis**:
   - LLM analyzes diffs with RAG context
   - Identifies issues in changed code
   - Suggests improvements

4. **Store Results**:
   - Create `CodeAnalysis` record
   - Create `CodeAnalysisIssue` for each issue found
   - Link to `PullRequest` entity

### PR Re-Analysis

When a PR is updated (new commits pushed):

1. **Fetch Updated Diffs**:
   - Get latest changes since last analysis
   - Or re-analyze entire PR (configurable)

2. **Include Previous Issues**:
   - Fetch issues from previous CodeAnalysis
   - Include in request as `previous_issues`
   - AI can check if issues were addressed

3. **Update Analysis**:
   - Update existing `CodeAnalysis` record
   - Mark old issues as outdated
   - Create new issues for new findings
   - Reuse issue records if still present

### Request Structure

```json
{
  "project_id": "uuid",
  "analysis_type": "PULL_REQUEST",
  "repository": {
    "workspace": "my-workspace",
    "repo_slug": "my-repo",
    "branch": "feature/new-feature",
    "target_branch": "main"
  },
  "changed_files": [
    {
      "path": "src/controller/UserController.java",
      "diff": "@@ -15,3 +15,5 @@...",
      "old_content": "...",
      "new_content": "..."
    }
  ],
  "previous_issues": [
    {
      "id": "issue-uuid",
      "file": "src/controller/UserController.java",
      "line": 23,
      "description": "Missing input validation"
    }
  ],
  "metadata": {
    "pr_number": 123,
    "pr_title": "Add user authentication",
    "author": "dev-user",
    "source_branch": "feature/new-feature",
    "target_branch": "main",
    "reviewers": ["reviewer1", "reviewer2"]
  }
}
```

### Response Processing

```json
{
  "issues": [
    {
      "file": "src/controller/UserController.java",
      "line": 23,
      "severity": "HIGH",
      "category": "SECURITY",
      "description": "SQL injection vulnerability in login method",
      "suggestion": "Use parameterized queries",
      "code_snippet": "String query = \"SELECT * FROM users WHERE...\";"
    },
    {
      "file": "src/controller/UserController.java",
      "line": 45,
      "severity": "MEDIUM",
      "category": "CODE_QUALITY",
      "description": "Method too long (120 lines)",
      "suggestion": "Refactor into smaller methods"
    }
  ],
  "summary": {
    "total_issues": 2,
    "by_severity": {"HIGH": 1, "MEDIUM": 1},
    "by_category": {"SECURITY": 1, "CODE_QUALITY": 1}
  },
  "previous_issues_status": [
    {
      "issue_id": "issue-uuid",
      "resolved": false,
      "comment": "Issue still present"
    }
  ]
}
```

### Database Records

**CodeAnalysis**:
```java
{
  id: "analysis-uuid",
  projectId: "proj-uuid",
  pullRequestId: "pr-uuid",
  status: "COMPLETED",
  createdAt: "2024-01-15T10:30:00",
  completedAt: "2024-01-15T10:32:00",
  totalIssues: 2,
  highSeverity: 1,
  mediumSeverity: 1,
  lowSeverity: 0
}
```

**CodeAnalysisIssue**:
```java
{
  id: "issue-uuid",
  analysisId: "analysis-uuid",
  file: "src/controller/UserController.java",
  line: 23,
  severity: "HIGH",
  category: "SECURITY",
  description: "SQL injection vulnerability...",
  suggestion: "Use parameterized queries",
  codeSnippet: "String query = ...",
  resolved: false
}
```

### RAG Integration

**Query for Context**:
```python
POST /query
{
  "project_id": "proj-123",
  "repository": "my-repo",
  "branch": "main",  # Target branch
  "query": "authentication login user validation",
  "top_k": 10
}
```

**Response**:
```json
{
  "results": [
    {
      "file": "src/service/AuthService.java",
      "content": "public class AuthService { ... }",
      "score": 0.92
    },
    {
      "file": "docs/security.md",
      "content": "# Security Guidelines...",
      "score": 0.85
    }
  ]
}
```

AI receives this context to better understand codebase patterns and standards.

### Diff Analysis

**Added Lines**: Primary focus for new issues  
**Modified Lines**: Check for improvements or regressions  
**Removed Lines**: Context only, issues here are resolved  

**Diff Parsing**:
```
@@ -10,5 +10,7 @@ class UserService {
-  String query = "SELECT * FROM users WHERE id=" + userId;
+  PreparedStatement stmt = conn.prepareStatement(
+    "SELECT * FROM users WHERE id=?"
+  );
```

AI identifies:
- Removed code: SQL injection vulnerability
- Added code: Properly uses prepared statement
- Resolution: Previous issue fixed

### Issue Categorization

**Severity Levels**:
- `HIGH`: Security vulnerabilities, critical bugs, data loss risks
- `MEDIUM`: Code quality issues, performance problems, maintainability
- `LOW`: Style issues, minor improvements, optimization suggestions

**Categories**:
- `SECURITY`: Security vulnerabilities, authentication, authorization
- `CODE_QUALITY`: Code smells, complexity, duplication
- `PERFORMANCE`: Inefficiencies, resource usage, bottlenecks
- `BEST_PRACTICES`: Convention violations, anti-patterns
- `DOCUMENTATION`: Missing or incorrect documentation
- `TESTING`: Missing tests, test quality issues

### Webhook Response

After analysis completes, CodeCrow can optionally:

1. **Post PR Comment** (if configured):
   - Summary of findings
   - Link to detailed results
   - Severity breakdown

2. **Set PR Status**:
   - Pass/Fail based on thresholds
   - Block merge if critical issues found

3. **Notify Reviewers**:
   - Email or Slack notifications
   - Issue summary and link

## Comparison: Branch vs PR Analysis

| Aspect | Branch Analysis | Pull Request Analysis |
|--------|----------------|----------------------|
| Trigger | PR merge (push event) | PR create/update |
| Scope | Changed files in merge | Changed files in PR |
| Purpose | Verify issue resolution | Find issues in changes |
| Previous Issues | Branch issues | Previous PR analysis |
| RAG Indexing | Yes (full or incremental) | No (uses existing index) |
| Frequency | After each merge | After each PR update |
| Duration | Longer (indexing) | Faster (no indexing) |
| Result Storage | BranchIssue | CodeAnalysisIssue |

## Analysis Configuration

### Project Settings

Projects can configure:

- **Analysis Enabled**: Enable/disable automated analysis
- **Auto-Comment**: Post analysis summary to PR
- **Block on Critical**: Prevent merge if critical issues found
- **Severity Threshold**: Minimum severity to report
- **File Filters**: Exclude paths (e.g., `node_modules/`, `*.test.js`)
- **Max Analysis Time**: Timeout for analysis operation

### Analysis Scope Configuration

Control which branches trigger automated analysis using pattern matching. Configure in **Project Settings → Analysis Scope**.

**PR Target Branch Patterns**:
Only analyze PRs targeting branches matching these patterns.

**Branch Push Patterns**:
Only analyze pushes (including PR merges) to branches matching these patterns.

**Pattern Syntax**:
| Pattern | Description | Example Matches |
|---------|-------------|-----------------|
| `main` | Exact match | `main` |
| `develop` | Exact match | `develop` |
| `release/*` | Single-level wildcard | `release/1.0`, `release/2.0` |
| `feature/**` | Multi-level wildcard | `feature/auth`, `feature/auth/oauth` |
| `hotfix-*` | Prefix match | `hotfix-123`, `hotfix-urgent` |

**Examples**:
```
# Analyze PRs targeting main and develop branches only
PR Target Branches: main, develop

# Also analyze PRs targeting any release branch
PR Target Branches: main, develop, release/*

# Analyze pushes to main only (for branch analysis)
Branch Push Patterns: main
```

**Default Behavior**: If no patterns are configured, all branches are analyzed.

### Webhook Configuration

**Bitbucket Webhook URL**:
```
https://your-domain.com:8082/api/v1/bitbucket-cloud/webhook
```

**Events to Enable**:
- Repository: Push
- Pull Request: Created, Updated, Merged

**Authentication Header**:
```
Authorization: Bearer <project-webhook-token>
```

Generate token in CodeCrow UI under Project Settings.

## Best Practices

### For Branch Analysis

- Enable on main/develop branches
- Configure analysis scope patterns to filter analysis (e.g., `main`, `develop`)
- Review resolved issues periodically
- Clean up old resolved issues
- Monitor RAG indexing performance
- Schedule manual re-indexing if needed

### For PR Analysis

- Analyze all PRs before merge
- Configure PR target patterns in Analysis Scope to focus on protected branches
- Use as required status check
- Review and address issues before merging
- Don't ignore security issues
- Use suggestions to improve code

### General

- Keep codebase patterns consistent
- Document coding standards
- Train team on issue categories
- Adjust severity thresholds as needed
- Monitor analysis costs (OpenRouter)
- Review false positives and improve prompts

