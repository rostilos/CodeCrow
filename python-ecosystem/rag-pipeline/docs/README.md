# CodeCrow RAG Pipeline

A powerful RAG (Retrieval-Augmented Generation) pipeline for code repositories, built with LlamaIndex and MongoDB.

## Features

- **Multi-source indexing**: Index entire repositories or specific files
- **Code-aware chunking**: Intelligent splitting that preserves code structure
- **Language detection**: Automatic detection of programming languages
- **Incremental updates**: Update only changed files
- **Namespace isolation**: Separate indices per workspace/project/branch
- **MongoDB storage**: Scalable vector and document storage
- **OpenAI embeddings**: High-quality semantic search
- **REST API**: Easy integration with existing systems

## Architecture

### Components

1. **Document Loader** (`loader.py`): Loads files from repositories
2. **Chunking** (`chunking.py`): Code-aware text splitting
3. **Index Manager** (`index_manager.py`): Manages vector indices
4. **Query Service** (`query_service.py`): Semantic search and context retrieval
5. **API** (`api.py`): FastAPI REST endpoints

### Storage Structure

Each namespace (workspace__project__branch) has:
- `{namespace}_docs`: Document store (MongoDB)
- `{namespace}_vectors`: Vector store (MongoDB Atlas Vector Search)
- `{namespace}_metadata`: Index metadata and statistics

## Installation

```bash
pip install -r requirements.txt
```

## Configuration

Create a `.env` file:

```env
MONGO_URI=mongodb://localhost:27017
MONGO_DB_NAME=codecrow_rag
OPENAI_API_KEY=your_openai_api_key_here
OPENAI_MODEL=text-embedding-3-small
```

## Usage

### Starting the API Server

```bash
python api.py
```

Or with uvicorn:

```bash
uvicorn api:app --host 0.0.0.0 --port 8001
```

### Docker

```bash
docker build -t codecrow-rag-pipeline .
docker run -p 8001:8001 --env-file .env codecrow-rag-pipeline
```

## API Endpoints

### Index Management

#### Index Repository
```bash
POST /index/repository
{
  "repo_path": "/path/to/repo",
  "workspace": "team",
  "project": "myproject",
  "branch": "main",
  "commit": "abc123"
}
```

#### Update Files (Incremental)
```bash
POST /index/update-files
{
  "file_paths": ["src/main.py", "src/utils.py"],
  "repo_base": "/path/to/repo",
  "workspace": "team",
  "project": "myproject",
  "branch": "main",
  "commit": "abc123"
}
```

#### Delete Files
```bash
POST /index/delete-files
{
  "file_paths": ["src/old_file.py"],
  "workspace": "team",
  "project": "myproject",
  "branch": "main"
}
```

#### Delete Index
```bash
DELETE /index/{workspace}/{project}/{branch}
```

#### Get Index Stats
```bash
GET /index/stats/{workspace}/{project}/{branch}
```

#### List All Indices
```bash
GET /index/list
```

### Querying

#### Semantic Search
```bash
POST /query/search
{
  "query": "authentication implementation",
  "workspace": "team",
  "project": "myproject",
  "branch": "main",
  "top_k": 10,
  "filter_language": "python"  // optional
}
```

#### Get PR Context
```bash
POST /query/pr-context
{
  "workspace": "team",
  "project": "myproject",
  "branch": "main",
  "changed_files": ["src/auth.py", "src/user.py"],
  "pr_description": "Added new authentication flow",
  "top_k": 10
}
```

## Programmatic Usage

```python
from codecrow_rag_pipeline import RAGConfig, RAGIndexManager, RAGQueryService

# Configure
config = RAGConfig(
    mongo_uri="mongodb://localhost:27017",
    openrouter_api_key="your-key"
)

# Index repository
index_manager = RAGIndexManager(config)
stats = index_manager.index_repository(
    repo_path="/path/to/repo",
    workspace="team",
    project="myproject",
    branch="main",
    commit="abc123"
)

# Query
query_service = RAGQueryService(config)
results = query_service.semantic_search(
    query="authentication implementation",
    workspace="team",
    project="myproject",
    branch="main",
    top_k=10
)
```

## Integration with CodeCrow Pipeline

### Webhook Handler

When a PR is created/updated:

1. **First PR**: Index entire repository
2. **Updates**: Incrementally update changed files
3. **Query**: Get relevant context for analysis

```python
# On PR creation (first time)
stats = index_manager.index_repository(
    repo_path="/tmp/repo_clone",
    workspace=workspace,
    project=project,
    branch=branch,
    commit=commit
)

# On PR update
stats = index_manager.update_files(
    file_paths=changed_files,
    repo_base="/tmp/repo_clone",
    workspace=workspace,
    project=project,
    branch=branch,
    commit=commit
)

# Get context for review
context = query_service.get_context_for_pr(
    workspace=workspace,
    project=project,
    branch=branch,
    changed_files=changed_files,
    pr_description=pr_description
)
```

## MongoDB Setup

### Atlas Vector Search Index

Create a vector search index on the vectors collection:

```json
{
  "fields": [
    {
      "type": "vector",
      "path": "embedding",
      "numDimensions": 1536,
      "similarity": "cosine"
    }
  ]
}
```

### Local MongoDB

```bash
docker run -d -p 27017:27017 --name mongodb mongo:latest
```

## Performance Considerations

- **Chunk Size**: Default 800 for code, 1000 for text
- **Overlap**: 200 tokens to preserve context
- **File Size Limit**: 1MB (configurable)
- **Excluded Patterns**: node_modules, .venv, build artifacts
- **Top K**: Default 10 results per query

## Language Support

Automatic detection for:
- Python, JavaScript, TypeScript, Java, Kotlin
- PHP, Go, Rust, C/C++, C#, Swift
- Ruby, Scala, R, Lua, Perl
- HTML, CSS, SCSS, Vue, Svelte
- Markdown, JSON, YAML, XML, TOML

## Error Handling

- Binary files are automatically skipped
- Large files (>1MB) are excluded
- Unicode decode errors are logged and skipped
- Missing repositories return empty results

## Monitoring

Check health:
```bash
GET /health
```

View logs for indexing progress and errors.

## License

Part of CodeCrow platform.

