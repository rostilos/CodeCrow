# Integration Test Suite — Python Ecosystem

Scripts to run integration tests for `inference-orchestrator` and `rag-pipeline`.

## Quick Start

```bash
# Run all integration tests
./run-tests.sh

# Run for a specific package
./run-tests.sh io
./run-tests.sh rag

# Verbose with fail-fast
./run-tests.sh -v --failfast

# Coverage check (default threshold: 60%)
./check-coverage.sh
./check-coverage.sh rag 70 --html
```

## Test Structure

```
inference-orchestrator/integration/
├── conftest.py                 # App fixtures, mocked services
├── test_health.py              # GET /health
├── test_auth_middleware.py      # X-Service-Secret middleware
├── test_review_endpoints.py    # POST /review (legacy + streaming)
├── test_command_endpoints.py   # POST /review/summarize, /review/ask
├── test_qa_documentation.py    # POST /qa-documentation
└── test_rag_client_http.py     # RagClient → RAG pipeline HTTP (respx)

rag-pipeline/integration/
├── conftest.py                 # App fixtures, mocked Qdrant/embedding
├── test_health.py              # GET /, GET /health
├── test_auth_middleware.py      # X-Service-Secret middleware
├── test_parse_endpoints.py     # POST /parse, /parse/batch
├── test_query_endpoints.py     # POST /query/search, /query/pr-context, /query/deterministic
└── test_index_endpoints.py     # POST /index/repository, branches, cleanup, stats
```

## What's Tested

Integration tests exercise the **full FastAPI stack** (HTTP → middleware → router → service)
with external dependencies mocked at the boundary:

- **Qdrant** → `unittest.mock.MagicMock`
- **Embedding models** → mock returning fixed 384-dim vectors
- **Redis queue consumers** → mocked to avoid real connections
- **LLM providers** → service-level mocks (no real API calls)
- **RAG ↔ IO HTTP** → `respx` for intercepting httpx calls

## Logs

Test output is saved to `test-run/` (git-ignored).
