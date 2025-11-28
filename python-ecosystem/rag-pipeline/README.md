# CodeCrow RAG Pipeline

A powerful RAG (Retrieval-Augmented Generation) pipeline for code repositories.

## Project Structure

```
codecrow-rag-pipeline/
├── src/
│   └── rag_pipeline/
│       ├── __init__.py
│       ├── api/              # FastAPI application
│       │   ├── __init__.py
│       │   └── api.py
│       ├── core/             # Core indexing functionality
│       │   ├── __init__.py
│       │   ├── chunking.py   # Code-aware text splitting
│       │   ├── index_manager.py  # Index management
│       │   └── loader.py     # Document loading
│       ├── models/           # Data models and configuration
│       │   ├── __init__.py
│       │   └── config.py
│       ├── services/         # High-level services
│       │   ├── __init__.py
│       │   ├── query_service.py
│       │   └── webhook_integration.py
│       └── utils/            # Utility functions
│           ├── __init__.py
│           └── utils.py
├── tests/                    # Unit tests
│   ├── __init__.py
│   └── test_rag.py
├── docs/                     # Documentation
│   ├── README.md
│   ├── INTEGRATION_GUIDE.md
│   ├── DEPLOYMENT.md
│   └── IMPLEMENTATION_SUMMARY.md
├── scripts/                  # Utility scripts
│   ├── setup.sh
│   ├── quick-start.sh
│   └── examples.py
├── main.py                   # Main entry point
├── setup.py                  # Package setup
├── requirements.txt          # Dependencies
├── Dockerfile                # Docker image
├── .env.sample              # Environment template
└── .gitignore               # Git ignore rules
```

## Quick Start

### Option 1: Docker (Recommended)

```bash
# Set API key
echo "OPENAI_API_KEY=sk-your-key" >> .env

# Start services
docker-compose up -d mongodb rag-pipeline

# Verify
curl http://localhost:8001/health
```

### Option 2: Local Development

```bash
# Run setup script
./scripts/setup.sh

# Activate virtual environment
source .venv/bin/activate

# Start server
python main.py
```

### Option 3: Install as Package

```bash
# Install in development mode
pip install -e .

# Use in code
from rag_pipeline import RAGIndexManager, RAGQueryService
```

## Usage

### Programmatic Usage

```python
from rag_pipeline import RAGConfig, RAGIndexManager, RAGQueryService

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
    branch="main"
)
```

### API Usage

```bash
# Start server
python main.py

# Health check
curl http://localhost:8001/health

# Index repository
curl -X POST http://localhost:8001/index/repository \
  -H "Content-Type: application/json" \
  -d @- <<EOF
{
  "repo_path": "/tmp/repo",
  "workspace": "team",
  "project": "demo",
  "branch": "main",
  "commit": "abc123"
}
EOF

# Semantic search
curl -X POST http://localhost:8001/query/search \
  -H "Content-Type: application/json" \
  -d @- <<EOF
{
  "query": "authentication function",
  "workspace": "team",
  "project": "demo",
  "branch": "main",
  "top_k": 5
}
EOF
```

## Documentation

- **[Complete Documentation](docs/README.md)** - Full module documentation
- **[Integration Guide](docs/INTEGRATION_GUIDE.md)** - How to integrate with Java components
- **[Deployment Guide](docs/DEPLOYMENT.md)** - Production deployment
- **[Implementation Details](docs/IMPLEMENTATION_SUMMARY.md)** - Technical architecture

## Running Tests

```bash
# Install in development mode
pip install -e .

# Run tests
pytest tests/

# Run with coverage
pytest tests/ --cov=rag_pipeline --cov-report=html
```

## Environment Variables

Required:
- `MONGO_URI` - MongoDB connection string
- `OPENAI_API_KEY` - OpenAI API key

Optional:
- `MONGO_DB_NAME` - Database name (default: codecrow_rag)
- `OPENAI_MODEL` - Embedding model (default: text-embedding-3-small)
- `CHUNK_SIZE` - Code chunk size (default: 800)
- `CHUNK_OVERLAP` - Chunk overlap (default: 200)

See `.env.sample` for full configuration options.

## Features

- ✅ **Repository Indexing** - Index entire repositories
- ✅ **Incremental Updates** - Update only changed files
- ✅ **Semantic Search** - Natural language code search
- ✅ **PR Context** - Automatic context retrieval for PRs
- ✅ **Code-Aware Chunking** - Preserves code structure
- ✅ **50+ Languages** - Auto language detection
- ✅ **Smart Filtering** - Excludes binaries and build artifacts
- ✅ **REST API** - Production-ready FastAPI server
- ✅ **Docker Support** - Easy deployment

## License

Part of CodeCrow platform.

