# Python Ecosystem — Unit Tests

Unit tests for the two Python packages in the CodeCrow platform.

## Packages

| Package                    | Location                                   | Tests | Coverage |
| -------------------------- | ------------------------------------------ | ----- | -------- |
| **inference-orchestrator** | `python-ecosystem/inference-orchestrator/` | 1040  | 86%      |
| **rag-pipeline**           | `python-ecosystem/rag-pipeline/`           | 808   | 85%      |

## Prerequisites

- Python 3.13+ (shared venv at `codecrow-public/.venv`)
- `pytest`, `pytest-cov`, `pytest-asyncio` installed in the venv

## Quick Start

```bash
# Run all Python unit tests
./run-tests.sh

# Run a specific package
./run-tests.sh io          # inference-orchestrator
./run-tests.sh rag         # rag-pipeline

# Verbose output
./run-tests.sh -v

# Stop on first failure
./run-tests.sh --failfast
```

## Coverage Check

```bash
# Check both packages (default threshold: 80%)
./check-coverage.sh

# Custom threshold
./check-coverage.sh --threshold 85

# Single package with HTML report
./check-coverage.sh io --html

# CI gate — exits non-zero if below threshold
./check-coverage.sh --threshold 80
```

## Test Structure

### inference-orchestrator (`tests/`)

- **conftest.py** — sys.modules-level mocks for LangChain, MCP, and LLM provider packages
- **test\_\*.py** — 45 test modules covering models, utils, orchestrator stages 0-3, reconciliation, verification, command service, RAG client, etc.
- **pytest.ini** — `asyncio_mode = strict`, `pythonpath = src`

### rag-pipeline (`tests/`)

- **conftest.py** — fixtures for FastAPI test client, mock Qdrant/Redis, tree-sitter parsers
- **test\_\*.py** — 30+ test modules covering API routes, chunking, indexing, search, reranking, parsers, etc.
- **pytest.ini** — `asyncio_mode = auto`

## Logs

Test run logs are written to `test-run/` with timestamps:

```
test-run/
  inference-orchestrator_20260407_143000.log
  rag-pipeline_20260407_143000.log
  inference-orchestrator_coverage_20260407_143000.log
```
