# Python Ecosystem

Python ecosystem consists of two FastAPI services: MCP client and RAG pipeline.

## Project Structure

```
python-ecosystem/
├── mcp-client/               # MCP client service
│   ├── main.py              # FastAPI application
│   ├── requirements.txt     # Python dependencies
│   ├── Dockerfile           # Container build
│   ├── codecrow-mcp-servers-1.0.jar  # Java MCP servers
│   ├── llm/                 # LLM integration
│   ├── model/               # Data models
│   ├── server/              # MCP server management
│   ├── service/             # Business logic
│   └── utils/               # Utilities
└── rag-pipeline/            # RAG service
    ├── main.py              # FastAPI application
    ├── requirements.txt     # Python dependencies
    ├── Dockerfile           # Container build
    ├── setup.py             # Package setup
    ├── src/                 # Source code
    │   ├── api/            # API routes
    │   ├── core/           # Core functionality
    │   ├── models/         # Data models
    │   └── services/       # RAG services
    ├── docs/               # Documentation
    └── tests/              # Unit tests
```

## MCP Client

### Overview

Modified MCP client that receives analysis requests from pipeline-agent, generates AI prompts with context from RAG, executes MCP tools, and returns analysis results.

**Port**: 8000  
**Framework**: FastAPI + Uvicorn  
**Key Libraries**: langchain-openai, mcp-use, httpx

### Architecture

```
Request → Prompt Generation → RAG Context Retrieval → 
MCP Tools Execution → LLM Analysis → Response Processing → Result
```

### Key Components

**main.py**:
FastAPI application entry point with routes:
- `POST /review` - Main analysis endpoint
- `GET /health` - Health check

**llm/client.py**:
LLM client for OpenRouter integration.
- Configures ChatOpenAI for OpenRouter
- Handles streaming responses
- Error handling and retries

**service/analysis_service.py**:
Core analysis orchestration:
- Receives analysis request
- Queries RAG for relevant context
- Builds prompts based on analysis type
- Executes MCP tools if needed
- Calls LLM with context
- Parses and structures response

**service/rag_service.py**:
RAG integration client:
- Queries RAG pipeline for code context
- Handles retrieval errors
- Formats context for prompts

**server/mcp_manager.py**:
MCP server lifecycle management:
- Loads Java MCP servers from JAR
- Manages server processes
- Provides tool execution interface

**model/analysis_request.py**:
Pydantic models for requests and responses:
```python
class AnalysisRequest:
    project_id: str
    analysis_type: str  # PULL_REQUEST or BRANCH
    repository: RepositoryInfo
    changed_files: List[ChangedFile]
    previous_issues: List[Issue]
    metadata: dict

class AnalysisResponse:
    issues: List[Issue]
    summary: AnalysisSummary
```

### Configuration

**.env**:
```bash
AI_CLIENT_PORT=8000
RAG_ENABLED=true
RAG_API_URL=http://localhost:8001

# Optional
OPENROUTER_API_KEY=sk-or-v1-...  # If not using system config
OPENROUTER_MODEL=anthropic/claude-3.5-sonnet
```

### Prompt Engineering

**Pull Request Analysis Prompt**:
```
You are a code review expert analyzing a pull request.

Repository: {workspace}/{repo}
Branch: {branch}
PR Author: {author}

Changed Files:
{file_diffs}

Previous Issues:
{previous_issues}

Relevant Code Context from RAG:
{rag_context}

Analyze the code changes for:
- Code quality issues
- Security vulnerabilities
- Performance problems
- Best practice violations
- Logic errors

Return structured JSON with issues.
```

**Branch Analysis Prompt**:
```
You are analyzing a merged branch to verify if previously reported issues are resolved.

Repository: {workspace}/{repo}
Branch: {branch}

Previously Reported Issues:
{branch_issues}

Changed Files in Merge:
{changed_files}

For each issue, determine if it has been resolved based on the changes.
Return JSON with issue_id and resolved status.
```

### MCP Tools Usage

Bitbucket MCP tools are used when additional repository context is needed:
- Fetch file content not in diff
- Get repository structure
- Access commit history
- Search code patterns

### Response Format

```json
{
  "issues": [
    {
      "file": "src/main/java/Example.java",
      "line": 42,
      "severity": "HIGH",
      "category": "SECURITY",
      "description": "SQL injection vulnerability detected",
      "suggestion": "Use parameterized queries or ORM",
      "code_snippet": "String query = \"SELECT * FROM users WHERE id=\" + userId;"
    }
  ],
  "summary": {
    "total_issues": 5,
    "by_severity": {
      "HIGH": 1,
      "MEDIUM": 3,
      "LOW": 1
    },
    "by_category": {
      "SECURITY": 1,
      "CODE_QUALITY": 2,
      "PERFORMANCE": 1,
      "BEST_PRACTICES": 1
    }
  }
}
```

### Running Locally

```bash
cd python-ecosystem/mcp-client
pip install -r requirements.txt
cp .env.sample .env
# Edit .env with configuration
uvicorn main:app --host 0.0.0.0 --port 8000
```

### Docker Build

```bash
cd python-ecosystem/mcp-client
docker build -t codecrow-mcp-client .
docker run -p 8000:8000 --env-file .env codecrow-mcp-client
```

## RAG Pipeline

### Overview

Retrieval-Augmented Generation service for indexing codebases and providing semantic search over code.

**Port**: 8001  
**Framework**: FastAPI + Uvicorn  
**Key Libraries**: llama-index, qdrant-client, openai

### Architecture

```
Index Request → File Processing → Chunking → Embedding → 
Qdrant Storage → Search Query → Vector Similarity → Context Return
```

### Key Components

**src/api/routes.py**:
FastAPI routes:
- `POST /index` - Create or update index
- `POST /query` - Search for relevant code
- `DELETE /index/{collection}` - Delete index
- `GET /health` - Health check
- `GET /status/{collection}` - Index status

**src/services/indexing_service.py**:
Code indexing:
- Processes source code files
- Chunks code intelligently
- Generates embeddings
- Stores in Qdrant
- Supports incremental updates

**src/services/query_service.py**:
Semantic search:
- Embeds query
- Vector similarity search
- Retrieves top-K results
- Returns with metadata

**src/core/chunking.py**:
Code chunking strategies:
- Language-aware chunking
- Function/class boundaries
- Configurable chunk size and overlap
- Preserves context

**src/core/embeddings.py**:
Embedding generation:
- OpenRouter-compatible OpenAI client
- Uses text-embedding models
- Batch processing
- Caching

**src/models/**:
Pydantic models for API:
```python
class IndexRequest:
    project_id: str
    repository: str
    branch: str
    files: List[SourceFile]
    incremental: bool = False

class QueryRequest:
    project_id: str
    repository: str
    branch: str
    query: str
    top_k: int = 10

class QueryResponse:
    results: List[SearchResult]
    total: int
```

### Configuration

**.env**:
```bash
# Qdrant
QDRANT_URL=http://localhost:6333
QDRANT_COLLECTION_PREFIX=codecrow

# OpenRouter
OPENROUTER_API_KEY=sk-or-v1-your-key
OPENROUTER_MODEL=openai/text-embedding-3-small

# Chunking
CHUNK_SIZE=800
CHUNK_OVERLAP=200
TEXT_CHUNK_SIZE=1000
TEXT_CHUNK_OVERLAP=200

# Retrieval
RETRIEVAL_TOP_K=10
SIMILARITY_THRESHOLD=0.7

# File Processing
MAX_FILE_SIZE_BYTES=1048576

# Server
SERVER_HOST=0.0.0.0
SERVER_PORT=8001

# Cache directories
HOME=/tmp
TIKTOKEN_CACHE_DIR=/tmp/.tiktoken_cache
TRANSFORMERS_CACHE=/tmp/.transformers_cache
HF_HOME=/tmp/.huggingface
LLAMA_INDEX_CACHE_DIR=/tmp/.llama_index
```

### Indexing Flow

**Full Index**:
1. Receive all repository files
2. Filter supported file types
3. Chunk each file
4. Generate embeddings
5. Store in Qdrant collection
6. Create metadata index

**Incremental Update**:
1. Receive changed files
2. Delete old chunks for changed files
3. Re-chunk modified files
4. Generate embeddings
5. Update Qdrant collection

### Collection Naming

Collections are named: `{prefix}_{project_id}_{branch_name}`

Example: `codecrow_proj123_main`

### Chunking Strategy

**Code Files** (.java, .py, .js, .ts, etc.):
- 800 character chunks
- 200 character overlap
- Preserve function boundaries when possible

**Text Files** (.md, .txt):
- 1000 character chunks
- 200 character overlap
- Preserve paragraph boundaries

**Metadata Stored**:
- File path
- Language
- Chunk index
- Line numbers
- Repository info

### Query Processing

1. Embed query using same model
2. Similarity search in Qdrant
3. Filter by similarity threshold (0.7 default)
4. Return top K results (10 default)
5. Include file path, chunk content, score

### API Examples

**Index Repository**:
```bash
curl -X POST http://localhost:8001/index \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "proj123",
    "repository": "my-repo",
    "branch": "main",
    "files": [
      {
        "path": "src/main.py",
        "content": "def main():\n    print(\"Hello\")"
      }
    ],
    "incremental": false
  }'
```

**Query Code**:
```bash
curl -X POST http://localhost:8001/query \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "proj123",
    "repository": "my-repo",
    "branch": "main",
    "query": "authentication implementation",
    "top_k": 5
  }'
```

**Response**:
```json
{
  "results": [
    {
      "file": "src/auth/auth_service.py",
      "content": "class AuthService:\n    def authenticate(self, username, password)...",
      "score": 0.89,
      "metadata": {
        "language": "python",
        "lines": "10-25"
      }
    }
  ],
  "total": 5
}
```

### Running Locally

```bash
cd python-ecosystem/rag-pipeline
pip install -r requirements.txt
cp .env.sample .env
# Edit .env with Qdrant URL and OpenRouter key
uvicorn main:app --host 0.0.0.0 --port 8001
```

### Docker Build

```bash
cd python-ecosystem/rag-pipeline
docker build -t codecrow-rag-pipeline .
docker run -p 8001:8001 --env-file .env codecrow-rag-pipeline
```

### Performance Considerations

**Indexing**:
- Large repositories may take several minutes
- Use incremental updates when possible
- Monitor Qdrant memory usage
- Consider batching for very large repos

**Querying**:
- Fast (<100ms for most queries)
- Adjust top_k to balance quality vs speed
- Similarity threshold affects precision/recall

**Storage**:
- Qdrant uses memory-mapped files
- Disk usage: ~1-2GB per 10k code files
- Regular collection cleanup recommended

## Dependencies

### MCP Client Requirements

```
asyncio
fastapi
uvicorn
pydantic
python-dotenv
httpx
langchain-openai==0.3.32
langchain-core==0.3.75
mcp-use
```

### RAG Pipeline Requirements

```
python-dotenv>=1.0.0
openai>=1.12.0
tiktoken>=0.5.0
llama-index-core==0.13.0
llama-index-embeddings-openai>=0.3.0
llama-index-vector-stores-qdrant>=0.5.0
llama-index-llms-openai>=0.3.0
qdrant-client>=1.7.0
fastapi>=0.109.0
uvicorn[standard]>=0.27.0
pydantic>=2.6.0
aiofiles>=23.2.0
pytest>=8.0.0
```

## Common Issues

### MCP Client Can't Load Java JAR
Ensure `codecrow-mcp-servers-1.0.jar` is in the mcp-client directory. Run `tools/production-build.sh` to copy it.

### RAG Pipeline Connection Failed
Verify Qdrant is running and accessible at configured URL.

### OpenRouter API Errors
Check API key is valid and has credits. Verify model name is correct.

### Embedding Generation Slow
Use smaller embedding model or reduce chunk size. Consider caching.

### Out of Memory
Reduce batch size for indexing. Increase container memory limits.

## Development Tips

- Use virtual environments: `python -m venv venv`
- Hot reload: `uvicorn main:app --reload`
- Check logs for detailed error messages
- Test RAG queries independently before integration
- Monitor OpenRouter usage and costs
- Use smaller models for development/testing

