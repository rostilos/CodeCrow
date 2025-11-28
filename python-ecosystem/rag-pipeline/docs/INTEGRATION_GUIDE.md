# Integration Guide: CodeCrow RAG Pipeline

This guide explains how to integrate the RAG pipeline with the existing CodeCrow platform.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    Bitbucket Cloud (Webhooks)                   │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│              codecrow-pipeline-agent (Java)                     │
│  - Receives webhooks                                            │
│  - Clones repository                                            │
│  - Sends to MCP client for analysis                             │
└──────────┬────────────────────────────────┬─────────────────────┘
           │                                │
           ▼                                ▼
┌──────────────────────────┐    ┌──────────────────────────────┐
│  codecrow-mcp-client     │    │  codecrow-rag-pipeline       │
│  (Python)                │    │  (Python)                    │
│  - MCP server comm       │    │  - Repository indexing       │
│  - Code analysis         │◄───┤  - Semantic search           │
│  - Review generation     │    │  - Context retrieval         │
└──────────────────────────┘    └──────────────────────────────┘
           │                                │
           ▼                                ▼
┌──────────────────────────┐    ┌──────────────────────────────┐
│  codecrow-mcp-servers    │    │  MongoDB Atlas               │
│  (Java - MCP tools)      │    │  - Vector store              │
└──────────────────────────┘    │  - Document store            │
                                 └──────────────────────────────┘
```

## Integration Points

### 1. Pipeline Agent Integration

The `codecrow-pipeline-agent` should call the RAG pipeline API when processing webhooks.

#### Java HTTP Client Example

Add to `codecrow-pipeline-agent`:

```java
// src/main/java/org/rostilos/codecrow/pipeline/service/RAGService.java
package org.rostilos.codecrow.pipeline.service;

import org.springframework.stereotype.Service;
import org.springframework.web.client.RestTemplate;
import org.springframework.http.*;
import java.util.List;
import java.util.Map;

@Service
public class RAGService {
    
    private final RestTemplate restTemplate;
    private final String ragApiUrl;
    
    public RAGService() {
        this.restTemplate = new RestTemplate();
        this.ragApiUrl = System.getenv().getOrDefault("RAG_API_URL", "http://localhost:8001");
    }
    
    public Map<String, Object> indexRepository(
        String repoPath,
        String workspace,
        String project,
        String branch,
        String commit
    ) {
        String url = ragApiUrl + "/index/repository";
        
        Map<String, String> request = Map.of(
            "repo_path", repoPath,
            "workspace", workspace,
            "project", project,
            "branch", branch,
            "commit", commit
        );
        
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        
        HttpEntity<Map<String, String>> entity = new HttpEntity<>(request, headers);
        
        ResponseEntity<Map> response = restTemplate.postForEntity(url, entity, Map.class);
        return response.getBody();
    }
    
    public Map<String, Object> updateFiles(
        List<String> filePaths,
        String repoBase,
        String workspace,
        String project,
        String branch,
        String commit
    ) {
        String url = ragApiUrl + "/index/update-files";
        
        Map<String, Object> request = Map.of(
            "file_paths", filePaths,
            "repo_base", repoBase,
            "workspace", workspace,
            "project", project,
            "branch", branch,
            "commit", commit
        );
        
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        
        HttpEntity<Map<String, Object>> entity = new HttpEntity<>(request, headers);
        
        ResponseEntity<Map> response = restTemplate.postForEntity(url, entity, Map.class);
        return response.getBody();
    }
    
    public String getPRContext(
        String workspace,
        String project,
        String branch,
        List<String> changedFiles,
        String prDescription,
        int topK
    ) {
        String url = ragApiUrl + "/query/pr-context";
        
        Map<String, Object> request = Map.of(
            "workspace", workspace,
            "project", project,
            "branch", branch,
            "changed_files", changedFiles,
            "pr_description", prDescription != null ? prDescription : "",
            "top_k", topK
        );
        
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        
        HttpEntity<Map<String, Object>> entity = new HttpEntity<>(request, headers);
        
        ResponseEntity<Map> response = restTemplate.postForEntity(url, entity, Map.class);
        Map<String, Object> body = response.getBody();
        
        return body != null ? (String) body.get("context") : "";
    }
    
    public void deleteBranchIndex(String workspace, String project, String branch) {
        String url = ragApiUrl + "/index/" + workspace + "/" + project + "/" + branch;
        restTemplate.delete(url);
    }
}
```

#### Update Webhook Handler

```java
// In your webhook handler class
@Autowired
private RAGService ragService;

@PostMapping("/webhook/pullrequest/created")
public ResponseEntity<String> handlePRCreated(@RequestBody Map<String, Object> payload) {
    // Extract data from webhook
    String workspace = extractWorkspace(payload);
    String project = extractProject(payload);
    String branch = extractBranch(payload);
    String commit = extractCommit(payload);
    
    // Clone repository
    String repoPath = cloneRepository(workspace, project, branch);
    
    try {
        // Index repository in RAG
        Map<String, Object> ragResult = ragService.indexRepository(
            repoPath, workspace, project, branch, commit
        );
        
        logger.info("RAG indexing result: {}", ragResult);
        
        // Continue with regular analysis...
        
    } catch (Exception e) {
        logger.error("RAG indexing failed: {}", e.getMessage());
        // Continue without RAG context
    }
    
    return ResponseEntity.ok("Processed");
}

@PostMapping("/webhook/pullrequest/updated")
public ResponseEntity<String> handlePRUpdated(@RequestBody Map<String, Object> payload) {
    // Extract data
    String workspace = extractWorkspace(payload);
    String project = extractProject(payload);
    String branch = extractBranch(payload);
    String commit = extractCommit(payload);
    List<String> changedFiles = extractChangedFiles(payload);
    
    String repoPath = cloneRepository(workspace, project, branch);
    
    try {
        // Update files in RAG
        ragService.updateFiles(
            changedFiles, repoPath, workspace, project, branch, commit
        );
        
    } catch (Exception e) {
        logger.error("RAG update failed: {}", e.getMessage());
    }
    
    return ResponseEntity.ok("Processed");
}
```

### 2. MCP Client Integration

The `codecrow-mcp-client` should use RAG context when generating reviews.

#### Update review_service.py

Add to `codecrow-mcp-client/service/review_service.py`:

```python
import requests
from typing import List, Optional

class RAGContextProvider:
    """Provides RAG context for code review"""
    
    def __init__(self, rag_api_url: str = "http://localhost:8001"):
        self.rag_api_url = rag_api_url
    
    def get_pr_context(
        self,
        workspace: str,
        project: str,
        branch: str,
        changed_files: List[str],
        pr_description: Optional[str] = None,
        top_k: int = 10
    ) -> str:
        """Get relevant context from RAG"""
        try:
            response = requests.post(
                f"{self.rag_api_url}/query/pr-context",
                json={
                    "workspace": workspace,
                    "project": project,
                    "branch": branch,
                    "changed_files": changed_files,
                    "pr_description": pr_description,
                    "top_k": top_k
                },
                timeout=30
            )
            
            if response.status_code == 200:
                return response.json().get("context", "")
            else:
                logger.warning(f"RAG API returned status {response.status_code}")
                return ""
                
        except Exception as e:
            logger.error(f"Failed to get RAG context: {e}")
            return ""

# In your review service
class ReviewService:
    def __init__(self):
        self.rag_provider = RAGContextProvider()
    
    async def generate_review(self, workspace, project, branch, files, pr_description):
        # Get RAG context
        rag_context = self.rag_provider.get_pr_context(
            workspace=workspace,
            project=project,
            branch=branch,
            changed_files=[f['path'] for f in files],
            pr_description=pr_description
        )
        
        # Build prompt with RAG context
        prompt = self._build_prompt_with_context(files, rag_context)
        
        # Continue with regular review generation...
```

#### Update prompt_builder.py

```python
def build_prompt_with_rag_context(files: List[dict], rag_context: str) -> str:
    """Build prompt including RAG context"""
    
    prompt = "# Code Review Request\n\n"
    
    if rag_context:
        prompt += "## Related Code Context (from repository)\n\n"
        prompt += rag_context
        prompt += "\n\n"
    
    prompt += "## Changed Files\n\n"
    for file in files:
        prompt += f"### {file['path']}\n"
        prompt += f"```{file.get('language', '')}\n"
        prompt += file['content']
        prompt += "\n```\n\n"
    
    prompt += "## Instructions\n"
    prompt += "Review the code changes considering the related context above.\n"
    prompt += "Focus on: bugs, security issues, best practices, and code quality.\n"
    
    return prompt
```

### 3. Environment Configuration

Update `.env` files in each module:

#### codecrow-pipeline-agent/.env
```env
RAG_API_URL=http://codecrow-rag-pipeline:8001
RAG_ENABLED=true
```

#### codecrow-mcp-client/.env
```env
RAG_API_URL=http://codecrow-rag-pipeline:8001
RAG_ENABLED=true
RAG_TOP_K=10
```

#### codecrow-rag-pipeline/.env
```env
MONGO_URI=mongodb://mongodb:27017
MONGO_DB_NAME=codecrow_rag
OPENAI_API_KEY=sk-...
OPENAI_MODEL=text-embedding-3-small
```

### 4. Docker Compose Integration

Update `docker-compose.yml`:

```yaml
services:
  # Existing services...
  
  mongodb:
    image: mongo:7.0
    container_name: codecrow-mongodb
    ports:
      - "27017:27017"
    volumes:
      - mongodb_data:/data/db
    environment:
      MONGO_INITDB_DATABASE: codecrow_rag
    networks:
      - codecrow-network
  
  codecrow-rag-pipeline:
    build:
      context: ./codecrow-rag-pipeline
      dockerfile: Dockerfile
    container_name: codecrow-rag-pipeline
    ports:
      - "8001:8001"
    environment:
      - MONGO_URI=mongodb://mongodb:27017
      - MONGO_DB_NAME=codecrow_rag
      - OPENROUTER_API_KEY=${OPENROUTER_API_KEY}
      - OPENROUTER_MODEL=${OPENROUTER_MODEL:-openai/text-embedding-3-small}
    depends_on:
      - mongodb
    networks:
      - codecrow-network
    volumes:
      - ./codecrow-rag-pipeline:/app
  
  codecrow-pipeline-agent:
    # ... existing config ...
    environment:
      # ... existing env vars ...
      - RAG_API_URL=http://codecrow-rag-pipeline:8001
      - RAG_ENABLED=true
    depends_on:
      - codecrow-rag-pipeline
  
  codecrow-mcp-client:
    # ... existing config ...
    environment:
      # ... existing env vars ...
      - RAG_API_URL=http://codecrow-rag-pipeline:8001
      - RAG_ENABLED=true
    depends_on:
      - codecrow-rag-pipeline

volumes:
  mongodb_data:
  # ... existing volumes ...

networks:
  codecrow-network:
    driver: bridge
```

### 5. Workflow

#### First PR on a Branch
1. Webhook received → `codecrow-pipeline-agent`
2. Repository cloned to `/tmp/repo_clone`
3. RAG API called: `POST /index/repository`
4. Entire repository indexed (documents chunked and embedded)
5. MCP client called for analysis
6. RAG context retrieved: `POST /query/pr-context`
7. Review generated with context
8. Results sent to Bitbucket

#### PR Updated
1. Webhook received with changed files list
2. Repository updated
3. RAG API called: `POST /index/update-files`
4. Only changed files re-indexed
5. Analysis continues with updated context

#### Branch Deleted/Merged
1. Webhook received
2. RAG API called: `DELETE /index/{workspace}/{project}/{branch}`
3. Index cleaned up to save storage

## Testing

### Test RAG API

```bash
# Test health
curl http://localhost:8001/health

# Test indexing
curl -X POST http://localhost:8001/index/repository \
  -H "Content-Type: application/json" \
  -d '{
    "repo_path": "/tmp/test-repo",
    "workspace": "test",
    "project": "demo",
    "branch": "main",
    "commit": "abc123"
  }'

# Test query
curl -X POST http://localhost:8001/query/pr-context \
  -H "Content-Type: application/json" \
  -d '{
    "workspace": "test",
    "project": "demo",
    "branch": "main",
    "changed_files": ["src/main.py"],
    "pr_description": "Updated main logic",
    "top_k": 5
  }'
```

### Integration Test

Create a test script in `codecrow-pipeline-agent`:

```java
@SpringBootTest
public class RAGIntegrationTest {
    
    @Autowired
    private RAGService ragService;
    
    @Test
    public void testRAGWorkflow() {
        // Index repository
        Map<String, Object> result = ragService.indexRepository(
            "/tmp/test-repo", "test", "demo", "main", "abc123"
        );
        
        assertNotNull(result);
        assertTrue((Boolean) result.get("success"));
        
        // Get context
        String context = ragService.getPRContext(
            "test", "demo", "main",
            List.of("src/main.py"),
            "Updated main logic",
            10
        );
        
        assertNotNull(context);
        assertFalse(context.isEmpty());
    }
}
```

## Performance Considerations

### Indexing Performance
- First PR: ~1-5 seconds per 100 files
- Updates: ~100-500ms per file
- Large repos (1000+ files): Consider background indexing

### Query Performance
- Semantic search: ~50-200ms
- Context retrieval: ~100-500ms
- Depends on MongoDB Atlas tier

### Optimization Tips
1. Use MongoDB Atlas M10+ for production
2. Enable vector search index
3. Cache frequently accessed contexts
4. Implement request queuing for large repos
5. Use incremental updates instead of full reindex

## Monitoring

Add health checks and metrics:

```java
@RestController
@RequestMapping("/monitoring")
public class RAGMonitoringController {
    
    @Autowired
    private RAGService ragService;
    
    @GetMapping("/rag/health")
    public ResponseEntity<Map<String, Object>> checkRAGHealth() {
        try {
            // Call RAG health endpoint
            String url = ragApiUrl + "/health";
            Map<String, Object> health = restTemplate.getForObject(url, Map.class);
            return ResponseEntity.ok(health);
        } catch (Exception e) {
            return ResponseEntity.status(503).body(
                Map.of("status", "unhealthy", "error", e.getMessage())
            );
        }
    }
    
    @GetMapping("/rag/stats")
    public ResponseEntity<List<Map>> getRAGStats() {
        String url = ragApiUrl + "/index/list";
        List<Map> indices = restTemplate.getForObject(url, List.class);
        return ResponseEntity.ok(indices);
    }
}
```

## Troubleshooting

### RAG API not responding
- Check if container is running: `docker ps | grep rag`
- Check logs: `docker logs codecrow-rag-pipeline`
- Verify MongoDB connection
- Check OpenAI API key

### Indexing failures
- Check file permissions
- Verify repository path exists
- Check MongoDB storage space
- Review excluded patterns

### Poor search results
- Verify embeddings are generated correctly
- Check chunk size configuration
- Review query formulation
- Ensure vector index is created in MongoDB

## Next Steps

1. Deploy MongoDB Atlas cluster
2. Configure OpenAI API key
3. Update docker-compose.yml
4. Test with a sample repository
5. Monitor performance
6. Tune chunk sizes and retrieval parameters
7. Add caching layer if needed

