# RAG Pipeline Testing Toolkit

A comprehensive manual testing toolkit for the CodeCrow RAG (Retrieval-Augmented Generation) pipeline.

## Overview

This toolkit provides tools to test the RAG pipeline's core functionality:

- **Indexing**: Full repository indexing with AST-based chunking
- **Semantic Search**: Query-based code retrieval with reranking
- **PR Context**: Multi-branch context retrieval for PR reviews
- **Deterministic Context**: Metadata-based retrieval using tree-sitter data

## Directory Structure

```
rag/
├── README.md                    # This file
├── config.py                    # Test configuration (URLs, credentials)
├── requirements.txt             # Python dependencies
│
├── fixtures/                    # Test data fixtures
│   ├── sample_repo/             # Mini repository for indexing tests
│   │   ├── src/                 # Sample source files (Python, Java, TS)
│   │   └── README.md
│   ├── pr_scenarios/            # PR context test scenarios
│   │   ├── simple_change.json
│   │   ├── multi_file_change.json
│   │   └── cross_file_deps.json
│   └── expected_results/        # Expected retrieval results for validation
│
├── scripts/                     # Test execution scripts
│   ├── test_indexing.py         # Index test repository
│   ├── test_search.py           # Semantic search tests
│   ├── test_pr_context.py       # PR context retrieval tests
│   ├── test_deterministic.py    # Deterministic context tests
│   ├── test_chunking.py         # AST chunking quality tests
│   └── run_all_tests.py         # Run full test suite
│
├── utils/                       # Shared utilities
│   ├── api_client.py            # RAG API client wrapper
│   ├── result_analyzer.py       # Result quality analysis
│   ├── report_generator.py      # Test report generation
│   └── mock_data_generator.py   # Generate mock PR data
│
└── reports/                     # Test execution reports (gitignored)
```

## Quick Start

### 1. Setup Environment

```bash
cd tools/test-suite/rag
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure

Edit `config.py` to set your RAG pipeline URL and credentials:

```python
RAG_API_URL = "http://localhost:8001"  # Local dev
# RAG_API_URL = "http://rag-pipeline:8001"  # Docker
```

### 3. Run Tests

```bash
# Run all tests
python scripts/run_all_tests.py

# Run specific test suite
python scripts/test_indexing.py
python scripts/test_search.py --query "user authentication"
python scripts/test_pr_context.py --scenario simple_change
```

## Test Scenarios

### Indexing Tests

Tests the full repository indexing flow:

1. Index sample repository
2. Verify chunk count and metadata extraction
3. Validate AST-based splitting (functions, classes)
4. Check tree-sitter metadata (semantic_names, imports, etc.)

### Semantic Search Tests

Tests query-based retrieval:

1. Simple keyword queries
2. Natural language queries
3. Code snippet queries
4. Language-filtered queries
5. Instruction-type queries (DEPENDENCY, LOGIC, IMPACT)

### PR Context Tests

Tests PR review context retrieval:

1. Single file changes
2. Multi-file changes with cross-dependencies
3. Branch-aware retrieval (target + base branch)
4. Priority reranking validation
5. Query decomposition verification

### Deterministic Context Tests

Tests metadata-based retrieval:

1. Changed file chunk retrieval
2. Related definition discovery (primary_name)
3. Class context (parent_class)
4. Namespace context
5. Import/extend chain resolution

## Fixtures

### Sample Repository

A minimal codebase designed to test RAG retrieval patterns:

- `src/services/user_service.py` - Python service with dependencies
- `src/services/auth_service.py` - Authentication with user imports
- `src/models/user.py` - User model (referenced by services)
- `src/api/user_controller.ts` - TypeScript API (cross-language)
- `src/utils/helpers.java` - Java utilities

### PR Scenarios

JSON files describing PR changes for testing:

```json
{
  "name": "simple_change",
  "workspace": "test",
  "project": "sample-repo",
  "branch": "feature/update-user",
  "base_branch": "main",
  "changed_files": ["src/services/user_service.py"],
  "diff_snippets": ["def update_user(self, user_id, data):"],
  "expected": {
    "should_find": ["UserRepository", "User model"],
    "should_not_find": ["notification", "email"]
  }
}
```

## Result Analysis

The toolkit provides analysis tools to evaluate retrieval quality:

### Metrics

- **Precision**: % of retrieved chunks that are relevant
- **Recall**: % of expected results found
- **MRR (Mean Reciprocal Rank)**: How high relevant results appear
- **Diversity**: Coverage across different files/types

### Reports

Test runs generate reports in `reports/` directory:

```
reports/
├── 2024-01-15_full_suite.json   # Raw results
├── 2024-01-15_full_suite.html   # HTML report
└── summary.md                    # Quick summary
```

## Integration with CI/CD

The toolkit can be integrated into CI pipelines:

```yaml
# .github/workflows/rag-tests.yml
- name: Run RAG Tests
  run: |
    cd tools/test-suite/rag
    pip install -r requirements.txt
    python scripts/run_all_tests.py --ci --output json
```

## Extending the Toolkit

### Adding New Test Scenarios

1. Create a JSON file in `fixtures/pr_scenarios/`
2. Define changed files, diffs, and expected results
3. Run with `python scripts/test_pr_context.py --scenario your_scenario`

### Adding New Sample Code

1. Add files to `fixtures/sample_repo/src/`
2. Re-index: `python scripts/test_indexing.py --reindex`
3. Update expected results in `fixtures/expected_results/`

## Troubleshooting

### Common Issues

1. **Connection refused**: Ensure RAG pipeline is running at configured URL
2. **Empty results**: Check if repository is indexed (`GET /index/list`)
3. **Low relevance scores**: Adjust `min_relevance_score` in tests

### Debug Mode

Run tests with debug logging:

```bash
python scripts/test_search.py --debug --query "your query"
```

This shows:

- Raw API responses
- Query decomposition
- Score calculations
- Chunk metadata
