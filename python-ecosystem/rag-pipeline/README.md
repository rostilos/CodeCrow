# CodeCrow RAG Pipeline

A high-performance RAG (Retrieval-Augmented Generation) pipeline optimized for code repositories with intelligent chunking, incremental indexing, and multi-language support.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Indexing Flow](#indexing-flow)
  - [Full Repository Indexing](#full-repository-indexing)
  - [Incremental Indexing](#incremental-indexing)
- [Chunking Strategy](#chunking-strategy)
  - [Code-Aware Splitting](#code-aware-splitting)
  - [Function-Aware Splitting](#function-aware-splitting)
- [Query Pipeline](#query-pipeline)
  - [Query Decomposition](#query-decomposition)
  - [Priority-Based Reranking](#priority-based-reranking)
- [Embedding & Vector Store](#embedding--vector-store)
- [Optimizations](#optimizations)
- [API Reference](#api-reference)
- [Configuration](#configuration)
- [Deployment](#deployment)

---

## Overview

The CodeCrow RAG Pipeline provides semantic code search and contextual retrieval for AI-powered code reviews. It indexes repository code into a vector database (Qdrant) and retrieves relevant context during PR reviews.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                          CodeCrow RAG Pipeline                               │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐                  │
│  │  Repository  │────▶│  Document    │────▶│  Code-Aware  │                  │
│  │  Scanner     │     │  Loader      │     │  Chunker     │                  │
│  └──────────────┘     └──────────────┘     └──────────────┘                  │
│         │                    │                    │                          │
│         │                    │                    │                          │
│         ▼                    ▼                    ▼                          │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐                  │
│  │   Filter     │     │   Language   │     │  Embedding   │                  │
│  │  (binaries)  │     │  Detection   │     │   Model      │                  │
│  └──────────────┘     └──────────────┘     └──────────────┘                  │
│                                                   │                          │
│                                                   │                          │
│                                                   ▼                          │
│                              ┌─────────────────────────────────────┐         │
│                              │           Qdrant                    │         │
│                              │        Vector Store                 │         │
│                              │   (Cosine Similarity Search)        │         │
│                              └─────────────────────────────────────┘         │
│                                                   │                          │
│                                                   │                          │
│                                                   ▼                          │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐                  │
│  │    Query     │────▶│   Priority   │────▶│   Results    │                  │
│  │ Decomposition│     │  Reranking   │     │   Merger     │                  │
│  └──────────────┘     └──────────────┘     └──────────────┘                  │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Key Features

| Feature | Description |
|---------|-------------|
| **Code-Aware Chunking** | Preserves function/class boundaries |
| **50+ Languages** | Auto language detection |
| **Incremental Updates** | Update only changed files |
| **Atomic Indexing** | Safe reindexing with rollback |
| **Query Decomposition** | Multi-query for better recall |
| **Priority Reranking** | Boost security-critical files |
| **Smart Filtering** | Excludes binaries, generated files |

---

## Architecture

### Project Structure

```
rag-pipeline/
├── main.py                           # FastAPI entry point
├── src/rag_pipeline/
│   ├── api/
│   │   └── api.py                    # REST API endpoints
│   ├── core/
│   │   ├── index_manager.py          # Index operations (full/incremental)
│   │   ├── chunking.py               # Code-aware text splitting
│   │   ├── loader.py                 # Document loading with filters
│   │   └── openrouter_embedding.py   # Embedding model wrapper
│   ├── services/
│   │   ├── query_service.py          # Semantic search with reranking
│   │   └── webhook_integration.py    # VCS webhook handlers
│   ├── models/
│   │   ├── config.py                 # Configuration models
│   │   └── instructions.py           # Query instruction types
│   └── utils/
│       └── utils.py                  # Helpers (language detection, etc.)
├── tests/
├── docs/
└── Dockerfile
```

### Component Diagram

```
┌────────────────────────────────────────────────────────────────────────────┐
│                           Component Interaction                            │
└────────────────────────────────────────────────────────────────────────────┘

                    ┌─────────────────────────────────────┐
                    │            FastAPI Server           │
                    │          (api/api.py)               │
                    └───────────────┬─────────────────────┘
                                    │
              ┌─────────────────────┼─────────────────────┐
              │                     │                     │
              ▼                     ▼                     ▼
     ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐
     │ RAGIndexManager │   │ RAGQueryService │   │    Webhooks     │
     │                 │   │                 │   │                 │
     │ • index_repo()  │   │ • semantic()    │   │ • push handler  │
     │ • update_files()│   │ • pr_context()  │   │ • pr handler    │
     │ • delete_files()│   │ • decompose()   │   │                 │
     └────────┬────────┘   └────────┬────────┘   └─────────────────┘
              │                     │
              ▼                     ▼
     ┌─────────────────┐   ┌─────────────────┐
     │ DocumentLoader  │   │  Embedding      │
     │                 │   │  Model          │
     │ • load_dir()    │   │                 │
     │ • load_files()  │   │ • _get_embed()  │
     │ • filter()      │   │ • _batch()      │
     └────────┬────────┘   └────────┬────────┘
              │                     │
              ▼                     ▼
     ┌─────────────────┐   ┌─────────────────┐
     │ CodeAware       │   │    Qdrant       │
     │ Splitter        │   │   Client        │
     │                 │   │                 │
     │ • split_docs()  │   │ • collections   │
     │ • by_language() │   │ • vectors       │
     └─────────────────┘   │ • search        │
                           └─────────────────┘
```

---

## Indexing Flow

### Full Repository Indexing

Full indexing creates a complete vector index of the repository with atomic swap for safety.

```
┌────────────────────────────────────────────────────────────────────────────┐
│                    Full Repository Indexing Flow                           │
└────────────────────────────────────────────────────────────────────────────┘

  POST /index/repository
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Step 1: SCAN REPOSITORY                                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│    repo_path: /tmp/repo/my-project                                         │
│         │                                                                   │
│         ▼                                                                   │
│    ┌─────────────────────────────────────────────────────────────────────┐  │
│    │  DocumentLoader.load_from_directory()                               │  │
│    │                                                                     │  │
│    │  For each file:                                                     │  │
│    │    ├── Check exclude patterns (node_modules, .git, dist)            │  │
│    │    ├── Check file size (< max_file_size_bytes)                      │  │
│    │    ├── Check if binary (magic bytes detection)                      │  │
│    │    ├── Detect language from extension                               │  │
│    │    └── Create Document with metadata                                │  │
│    └─────────────────────────────────────────────────────────────────────┘  │
│         │                                                                   │
│         ▼                                                                   │
│    Loaded: 150 documents                                                    │
│    Excluded: 2,500 files (node_modules, binaries, etc.)                     │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Step 2: CHUNKING                                                           │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│    ┌─────────────────────────────────────────────────────────────────────┐  │
│    │  CodeAwareSplitter.split_documents()                                │  │
│    │                                                                     │  │
│    │  For each document:                                                 │  │
│    │    ├── Check language (code vs text)                                │  │
│    │    ├── Select appropriate splitter:                                 │  │
│    │    │     Code: chunk_size=800, overlap=200, separator="\n\n"        │  │
│    │    │     Text: chunk_size=1000, overlap=200                         │  │
│    │    ├── Split into chunks                                            │  │
│    │    ├── Skip empty/whitespace chunks                                 │  │
│    │    ├── Truncate oversized chunks (>30k chars)                       │  │
│    │    └── Preserve metadata (path, language, chunk_index)              │  │
│    └─────────────────────────────────────────────────────────────────────┘  │
│         │                                                                   │
│         ▼                                                                   │
│    Created: 450 chunks from 150 files                                       │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Step 3: VALIDATE LIMITS                                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│    Check limits (configurable per plan):                                    │
│      ├── max_chunks_per_index: 10,000                                       │
│      └── max_files_per_index: 1,000                                         │
│                                                                             │
│    If exceeded:                                                             │
│      └── Raise ValueError with helpful message about exclude patterns       │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Step 4: ATOMIC COLLECTION SWAP                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│    Why atomic swap?                                                         │
│    • Old index remains available during reindexing                          │
│    • If indexing fails, old index is preserved                              │
│    • Zero downtime for queries                                              │
│                                                                             │
│    ┌─────────────────────────────────────────────────────────────────────┐  │
│    │  Collection Names:                                                  │  │
│    │    Final:  codecrow_myorg__myproject__main                          │  │
│    │    Temp:   codecrow_myorg__myproject__main_new                      │  │
│    └─────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│    Process:                                                                 │
│      1. Clean up any leftover temp collection                               │
│      2. Create temp collection with same config                             │
│      3. Index all chunks into temp collection                               │
│      4. Verify temp has data                                                │
│      5. Delete old final collection                                         │
│      6. Copy temp → final                                                   │
│      7. Delete temp collection                                              │
│                                                                             │
│    On failure:                                                              │
│      └── Delete temp, keep old intact                                       │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Step 5: BATCH EMBEDDING                                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│    ┌─────────────────────────────────────────────────────────────────────┐  │
│    │  Batch processing for efficiency:                                   │  │
│    │                                                                     │  │
│    │    embedding_batch_size = 100  (chunks per API call)                │  │
│    │    insert_batch_size = 100     (Qdrant upsert batch)                │  │
│    │                                                                     │  │
│    │    For batch in chunks[0::100]:                                     │  │
│    │      1. Extract texts from batch                                    │  │
│    │      2. Call embed_model._get_text_embeddings(texts)  ← Single API  │  │
│    │      3. Attach embeddings to nodes                                  │  │
│    │      4. Insert batch to Qdrant (no embedding call)                  │  │
│    │      5. Clear batch from memory                                     │  │
│    │      6. GC every 10 batches                                         │  │
│    │                                                                     │  │
│    │    On batch failure:                                                │  │
│    │      └── Retry individual chunks in failed batch                    │  │
│    └─────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Step 6: CLEANUP & RETURN                                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│    Memory cleanup:                                                          │
│      del chunks, documents, index, vector_store                             │
│      gc.collect()                                                           │
│                                                                             │
│    Return IndexStats:                                                       │
│      {                                                                      │
│        "namespace": "myorg__myproject__main",                               │
│        "document_count": 150,                                               │
│        "chunk_count": 450,                                                  │
│        "last_updated": "2024-12-27T10:30:00Z"                               │
│      }                                                                      │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Incremental Indexing

Incremental indexing updates only changed files, significantly faster for large repositories.

```
┌────────────────────────────────────────────────────────────────────────────┐
│                      Incremental Indexing Flow                             │
└────────────────────────────────────────────────────────────────────────────┘

  Webhook: push event (or manual trigger)
         │
         ▼
  Changed files: [src/auth.py, src/user.py]
  Deleted files: [src/old_module.py]
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  update_files() - For modified/added files                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│    Step 1: DELETE OLD VECTORS                                               │
│    ┌─────────────────────────────────────────────────────────────────────┐  │
│    │  qdrant_client.delete(                                              │  │
│    │    collection_name="codecrow_org__proj__main",                      │  │
│    │    points_selector=Filter(                                          │  │
│    │      must=[                                                         │  │
│    │        FieldCondition(                                              │  │
│    │          key="path",                                                │  │
│    │          match=MatchAny(any=["src/auth.py", "src/user.py"])         │  │
│    │        )                                                            │  │
│    │      ]                                                              │  │
│    │    )                                                                │  │
│    │  )                                                                  │  │
│    └─────────────────────────────────────────────────────────────────────┘  │
│         │                                                                   │
│         ▼                                                                   │
│    Step 2: LOAD NEW CONTENT                                                 │
│    ┌─────────────────────────────────────────────────────────────────────┐  │
│    │  DocumentLoader.load_specific_files(                                │  │
│    │    file_paths=[Path("src/auth.py"), Path("src/user.py")],          │  │
│    │    repo_base=repo_path,                                             │  │
│    │    workspace, project, branch, commit                               │  │
│    │  )                                                                  │  │
│    └─────────────────────────────────────────────────────────────────────┘  │
│         │                                                                   │
│         ▼                                                                   │
│    Step 3: SPLIT INTO CHUNKS                                                │
│    ┌─────────────────────────────────────────────────────────────────────┐  │
│    │  splitter.split_documents(documents)                                │  │
│    │  → 12 new chunks                                                    │  │
│    └─────────────────────────────────────────────────────────────────────┘  │
│         │                                                                   │
│         ▼                                                                   │
│    Step 4: INSERT NEW VECTORS                                               │
│    ┌─────────────────────────────────────────────────────────────────────┐  │
│    │  For batch in chunks[0::50]:                                        │  │
│    │    index.insert_nodes(batch)                                        │  │
│    └─────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  delete_files() - For removed files                                         │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│    ┌─────────────────────────────────────────────────────────────────────┐  │
│    │  qdrant_client.delete(                                              │  │
│    │    collection_name="codecrow_org__proj__main",                      │  │
│    │    points_selector=Filter(                                          │  │
│    │      must=[                                                         │  │
│    │        FieldCondition(                                              │  │
│    │          key="path",                                                │  │
│    │          match=MatchAny(any=["src/old_module.py"])                  │  │
│    │        )                                                            │  │
│    │      ]                                                              │  │
│    │    )                                                                │  │
│    │  )                                                                  │  │
│    └─────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Comparison: Full vs Incremental

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    Full vs Incremental Indexing                            │
├─────────────────────────┬───────────────────────────────────────────────────┤
│        Metric           │     Full Index      │   Incremental Index        │
├─────────────────────────┼─────────────────────┼─────────────────────────────┤
│  Files processed        │     All (1000)      │   Changed only (5)         │
│  Chunks generated       │     All (5000)      │   From changed (25)        │
│  Embedding API calls    │     50 batches      │   1 batch                  │
│  Time (medium repo)     │     2-5 minutes     │   5-15 seconds             │
│  Qdrant writes          │     Full replace    │   Delete + Insert          │
│  Index availability     │     Atomic swap     │   Always available         │
│  Use case               │     Initial/Rebuild │   Webhooks/CI              │
└─────────────────────────┴─────────────────────┴─────────────────────────────┘
```

---

## Chunking Strategy

### Code-Aware Splitting

Different chunking strategies for code vs documentation files.

```
┌────────────────────────────────────────────────────────────────────────────┐
│                       Code-Aware Splitting                                 │
└────────────────────────────────────────────────────────────────────────────┘

                     Document
                         │
                         ▼
              ┌─────────────────────┐
              │  Detect Language    │
              │  from metadata      │
              └─────────────────────┘
                         │
          ┌──────────────┴──────────────┐
          │                             │
          ▼                             ▼
   ┌─────────────┐               ┌─────────────┐
   │  CODE FILE  │               │  TEXT FILE  │
   │  .py, .ts,  │               │  .md, .txt, │
   │  .java, etc │               │  .rst, etc  │
   └──────┬──────┘               └──────┬──────┘
          │                             │
          ▼                             ▼
   ┌─────────────────────┐       ┌─────────────────────┐
   │  Code Splitter      │       │  Text Splitter      │
   │                     │       │                     │
   │  chunk_size: 800    │       │  chunk_size: 1000   │
   │  overlap: 200       │       │  overlap: 200       │
   │  separator: "\n\n"  │       │  separator: default │
   │                     │       │                     │
   │  Preserves:         │       │  Standard:          │
   │  • Function bounds  │       │  • Sentence split   │
   │  • Class structure  │       │  • Paragraph aware  │
   │  • Import blocks    │       │                     │
   └─────────────────────┘       └─────────────────────┘
```

### Code File Detection

```python
# Files detected as CODE (use code_splitter):
CODE_EXTENSIONS = {
    'py', 'pyx', 'pyi',              # Python
    'js', 'jsx', 'mjs', 'cjs',       # JavaScript
    'ts', 'tsx', 'mts', 'cts',       # TypeScript
    'java', 'kt', 'kts',             # JVM
    'go',                             # Go
    'rs',                             # Rust
    'c', 'cpp', 'cc', 'cxx', 'h',    # C/C++
    'cs',                             # C#
    'php', 'phtml',                   # PHP
    'rb',                             # Ruby
    'swift',                          # Swift
    'scala', 'sc',                    # Scala
    'ex', 'exs',                      # Elixir
    # ... 50+ extensions
}

# Files detected as TEXT (use text_splitter):
TEXT_EXTENSIONS = {
    'md', 'markdown',
    'txt',
    'rst',
    'adoc',
    'html', 'htm',
    'xml',
    'json', 'yaml', 'yml', 'toml'
}
```

### Function-Aware Splitting (Advanced)

For very large files, an advanced splitter preserves function boundaries:

```
┌────────────────────────────────────────────────────────────────────────────┐
│                    Function-Aware Splitting                                │
└────────────────────────────────────────────────────────────────────────────┘

Input: Large Python file (10,000 lines)
                │
                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  FunctionAwareSplitter._split_python()                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│    for line in lines:                                                       │
│        if line.startswith(('def ', 'class ', 'async def ')):               │
│            # Found boundary - check if current chunk is significant         │
│            if current_chunk and len(current_chunk) > 50:                    │
│                chunks.append(current_chunk)                                 │
│                current_chunk = []                                           │
│                                                                             │
│        current_chunk.append(line)                                           │
│                                                                             │
│        # Safety: if chunk exceeds max_size, force split                     │
│        if len('\n'.join(current_chunk)) > max_chunk_size:                   │
│            chunks.append(current_chunk)                                     │
│            current_chunk = []                                               │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘

Output chunks:
┌───────────────────────────────────────────────────┐
│ Chunk 1: imports + class UserService              │
│          Lines 1-300                              │
└───────────────────────────────────────────────────┘
┌───────────────────────────────────────────────────┐
│ Chunk 2: def authenticate()                       │
│          Lines 301-550                            │
└───────────────────────────────────────────────────┘
┌───────────────────────────────────────────────────┐
│ Chunk 3: def validate_token()                     │
│          def refresh_token()                      │
│          Lines 551-800                            │
└───────────────────────────────────────────────────┘
...
```

### Brace-Language Splitting (Java, TypeScript, Go, etc.)

```
┌────────────────────────────────────────────────────────────────────────────┐
│                   Brace-Language Splitting                                 │
└────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Algorithm:                                                                 │
│    1. Track brace_count: { +1, } -1                                         │
│    2. Detect function/class keywords                                        │
│    3. When brace_count returns to 0 after function → boundary               │
│                                                                             │
│  Example (Java):                                                            │
│                                                                             │
│    public class UserService {          ← brace_count = 1                   │
│        private String name;                                                 │
│                                                                             │
│        public void login() {           ← brace_count = 2, in_function=true │
│            // ...                                                           │
│        }                               ← brace_count = 1                   │
│        ─────── BOUNDARY ───────        ← in_function && brace_count=1      │
│                                                                             │
│        public void logout() {          ← brace_count = 2                   │
│            // ...                                                           │
│        }                               ← brace_count = 1                   │
│        ─────── BOUNDARY ───────                                             │
│    }                                   ← brace_count = 0                   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Chunk Metadata

Each chunk includes rich metadata for filtering and context:

```python
chunk_metadata = {
    "workspace": "my-org",
    "project": "my-repo",
    "branch": "main",
    "path": "src/auth/service.py",      # Relative path
    "commit": "abc123def",
    "language": "python",
    "filetype": "py",
    "chunk_index": 2,                    # Index within file
    "total_chunks": 5                    # Total chunks from file
}
```

---

## Query Pipeline

### Query Decomposition

Complex queries are decomposed into multiple targeted searches for better recall.

```
┌────────────────────────────────────────────────────────────────────────────┐
│                        Query Decomposition                                 │
└────────────────────────────────────────────────────────────────────────────┘

Input: PR Context Request
  • pr_title: "Fix authentication token refresh"
  • changed_files: ["src/auth/token.py", "src/auth/service.py", "src/user/profile.py"]
  • diff_snippets: ["def refresh_token(self):", "class TokenValidator:"]
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  _decompose_queries()                                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  A. INTENT QUERY (High Level) - Weight: 1.0                                 │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │ Query: "Fix authentication token refresh"                              │ │
│  │ Type: InstructionType.GENERAL                                          │ │
│  │ top_k: 10                                                              │ │
│  │                                                                        │ │
│  │ Purpose: Understand overall intent, find related features              │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│                                                                             │
│  B. FILE CONTEXT QUERIES (Mid Level) - Weight: 0.8                          │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │ Group files by directory (hotspot detection):                          │ │
│  │                                                                        │ │
│  │ src/auth/ → 2 files (token.py, service.py)                             │ │
│  │   Query: "logic in src/auth related to token.py, service.py"           │ │
│  │   Type: InstructionType.LOGIC                                          │ │
│  │   top_k: 5                                                             │ │
│  │                                                                        │ │
│  │ src/user/ → 1 file (profile.py)                                        │ │
│  │   Query: "logic in src/user related to profile.py"                     │ │
│  │   Type: InstructionType.LOGIC                                          │ │
│  │   top_k: 5                                                             │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│                                                                             │
│  C. SNIPPET QUERIES (Low Level) - Weight: 1.2 (High precision)              │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │ Query 1: "def refresh_token self"                                      │ │
│  │ Type: InstructionType.DEPENDENCY                                       │ │
│  │ top_k: 5                                                               │ │
│  │                                                                        │ │
│  │ Query 2: "class TokenValidator"                                        │ │
│  │ Type: InstructionType.DEPENDENCY                                       │ │
│  │ top_k: 5                                                               │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
         │
         ▼
Execute 5 queries in parallel → Merge results
```

### Priority-Based Reranking

Results are reranked based on file importance for code review.

```
┌────────────────────────────────────────────────────────────────────────────┐
│                      Priority-Based Reranking                              │
└────────────────────────────────────────────────────────────────────────────┘

Raw Results (by embedding similarity):
┌─────────────────────────────────────────────────────────────────────────────┐
│  #  │  File                      │  Raw Score  │  Priority  │              │
├─────┼────────────────────────────┼─────────────┼────────────┤              │
│  1  │  src/utils/helpers.py      │    0.89     │   MEDIUM   │              │
│  2  │  src/auth/service.py       │    0.87     │   HIGH     │  ← Security  │
│  3  │  tests/test_auth.py        │    0.85     │   LOW      │  ← Test      │
│  4  │  src/models/token.py       │    0.84     │   MEDIUM   │              │
│  5  │  src/security/jwt.py       │    0.82     │   HIGH     │  ← Security  │
└─────┴────────────────────────────┴─────────────┴────────────┴──────────────┘

After Priority Reranking:
┌─────────────────────────────────────────────────────────────────────────────┐
│  #  │  File                      │ Adj. Score  │  Boost     │              │
├─────┼────────────────────────────┼─────────────┼────────────┤              │
│  1  │  src/auth/service.py       │    1.00     │  ×1.3      │  ← Promoted  │
│  2  │  src/security/jwt.py       │    1.00     │  ×1.3      │  ← Promoted  │
│  3  │  src/utils/helpers.py      │    0.98     │  ×1.1      │              │
│  4  │  src/models/token.py       │    0.92     │  ×1.1      │              │
│  5  │  tests/test_auth.py        │    0.68     │  ×0.8      │  ← Demoted   │
└─────┴────────────────────────────┴─────────────┴────────────┴──────────────┘
```

### Priority Patterns

```python
# HIGH PRIORITY - Boost ×1.3
HIGH_PRIORITY_PATTERNS = [
    'service', 'controller', 'handler', 'api', 'core', 
    'auth', 'security', 'permission', 'repository', 'dao', 'migration'
]

# MEDIUM PRIORITY - Boost ×1.1
MEDIUM_PRIORITY_PATTERNS = [
    'model', 'entity', 'dto', 'schema', 'util', 'helper', 
    'common', 'shared', 'component', 'hook', 'client', 'integration'
]

# LOW PRIORITY - Penalty ×0.8
LOW_PRIORITY_PATTERNS = [
    'test', 'spec', 'config', 'mock', 'fixture', 'stub'
]
```

### Result Merging & Filtering

```
┌────────────────────────────────────────────────────────────────────────────┐
│                      Result Merging Pipeline                               │
└────────────────────────────────────────────────────────────────────────────┘

5 queries → ~50 raw results
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Step 1: DEDUPLICATE                                                        │
│  ─────────────────                                                          │
│  Key: file_path + hash(content)                                             │
│  Keep: Highest scoring occurrence                                           │
│  Result: ~30 unique results                                                 │
└─────────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Step 2: APPLY PRIORITY BOOST                                               │
│  ────────────────────────────                                               │
│  For each result:                                                           │
│    if path matches HIGH_PRIORITY: score *= 1.3 (cap at 1.0)                 │
│    if path matches MEDIUM_PRIORITY: score *= 1.1                            │
│    if path matches LOW_PRIORITY: score *= 0.8                               │
└─────────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Step 3: FILTER BY THRESHOLD                                                │
│  ──────────────────────────                                                 │
│  min_relevance_score: 0.7 (configurable)                                    │
│  Keep results with adjusted score >= threshold                              │
│  Result: ~15 high-quality results                                           │
└─────────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Step 4: FALLBACK (if too aggressive)                                       │
│  ─────────────────────────────────────                                      │
│  If filtered_results == 0 and raw_results > 0:                              │
│    Return top 5 by raw score (ignore threshold)                             │
└─────────────────────────────────────────────────────────────────────────────┘
         │
         ▼
Final: 15 relevant, deduplicated, priority-ranked results
```

---

## Embedding & Vector Store

### OpenRouter Embedding Model

```
┌────────────────────────────────────────────────────────────────────────────┐
│                     Embedding Configuration                                │
├────────────────────────────────────────────────────────────────────────────┤
│                                                                            │
│  Model: text-embedding-3-small (OpenAI via OpenRouter)                     │
│  Dimensions: 1536                                                          │
│  Max tokens per request: 8191                                              │
│                                                                            │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  OpenRouterEmbedding Configuration:                                  │  │
│  │                                                                      │  │
│  │  api_key: OPENROUTER_API_KEY                                         │  │
│  │  model: "openai/text-embedding-3-small"                              │  │
│  │  api_base: "https://openrouter.ai/api/v1"                            │  │
│  │  timeout: 60 seconds                                                 │  │
│  │  max_retries: 3                                                      │  │
│  │  expected_dim: 1536                                                  │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                            │
└────────────────────────────────────────────────────────────────────────────┘
```

### Qdrant Vector Store

```
┌────────────────────────────────────────────────────────────────────────────┐
│                       Qdrant Configuration                                 │
├────────────────────────────────────────────────────────────────────────────┤
│                                                                            │
│  URL: qdrant:6333 (Docker) or localhost:6333                               │
│                                                                            │
│  Collection naming: {prefix}_{workspace}__{project}__{branch}              │
│  Example: codecrow_myorg__myrepo__main                                     │
│                                                                            │
│  Vector Config:                                                            │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  VectorParams(                                                       │  │
│  │    size=1536,           # Match embedding dimensions                 │  │
│  │    distance=Distance.COSINE  # Cosine similarity                     │  │
│  │  )                                                                   │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                            │
│  Indexing Options:                                                         │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  enable_hybrid: False    # No sparse vectors (simplicity)            │  │
│  │  batch_size: 100         # Points per upsert                         │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                            │
└────────────────────────────────────────────────────────────────────────────┘
```

### Collection per Branch

Each workspace/project/branch combination gets its own collection:

```
Qdrant Collections:
├── codecrow_acme__frontend__main
├── codecrow_acme__frontend__develop
├── codecrow_acme__backend__main
├── codecrow_other__api__main
└── ...
```

---

## Optimizations

### 1. Batch Embedding

Reduces API calls by batching texts:

```
Without batching:     With batching:
450 chunks            450 chunks
450 API calls         5 API calls (100 per batch)
~450 seconds          ~15 seconds
```

### 2. Memory Management

Aggressive cleanup prevents OOM on large repos:

```python
# After each batch
del texts, embeddings
# Every 10 batches
gc.collect()
# After indexing
del chunks, documents, index
gc.collect()
```

### 3. Atomic Indexing

Safe reindexing without downtime:

```
Old Collection     Temp Collection     Final State
────────────────   ────────────────    ────────────────
[active queries]   [building...]       [active queries]
    ↓                   ↓                    ↓
[still active]     [complete]          [new data]
    ↓                   ↓                    ↓
[deleted]          [copied→final]      [serving]
```

### 4. Exclude Patterns

Default patterns to skip non-essential files:

```python
EXCLUDED_PATTERNS = [
    # Dependencies
    "node_modules/", "vendor/", ".venv/", "venv/",
    
    # Build artifacts
    "dist/", "build/", "target/", ".next/",
    
    # Version control
    ".git/", ".svn/",
    
    # IDE
    ".idea/", ".vscode/",
    
    # Lock files
    "package-lock.json", "yarn.lock", "poetry.lock",
    
    # Generated
    "*.min.js", "*.bundle.js", "*.map",
    "*.pb.go", "*_generated.*",
    
    # Binary
    "*.png", "*.jpg", "*.gif", "*.ico",
    "*.pdf", "*.zip", "*.tar", "*.gz"
]
```

### 5. Project-Specific Excludes

Users can add custom patterns in project settings:

```python
# From project config
extra_exclude_patterns = [
    "legacy/",           # Old code not in use
    "generated/",        # Auto-generated
    "docs/api/",         # API docs (auto-generated)
]
```

---

## API Reference

### POST /index/repository

Full repository indexing.

```json
{
  "repo_path": "/tmp/repo/my-project",
  "workspace": "my-org",
  "project": "my-repo",
  "branch": "main",
  "commit": "abc123def",
  "exclude_patterns": ["legacy/", "generated/"]
}
```

**Response:**
```json
{
  "namespace": "my-org__my-repo__main",
  "document_count": 150,
  "chunk_count": 450,
  "last_updated": "2024-12-27T10:30:00Z"
}
```

### POST /index/update

Incremental update.

```json
{
  "file_paths": ["src/auth/token.py", "src/user/profile.py"],
  "repo_base": "/tmp/repo/my-project",
  "workspace": "my-org",
  "project": "my-repo",
  "branch": "main",
  "commit": "def456ghi"
}
```

### DELETE /index/files

Remove files from index.

```json
{
  "file_paths": ["src/old_module.py"],
  "workspace": "my-org",
  "project": "my-repo",
  "branch": "main"
}
```

### POST /query/search

Semantic search.

```json
{
  "query": "authentication token refresh",
  "workspace": "my-org",
  "project": "my-repo",
  "branch": "main",
  "top_k": 10,
  "filter_language": "python"
}
```

**Response:**
```json
{
  "results": [
    {
      "text": "def refresh_token(self, user_id: str):\n    ...",
      "score": 0.92,
      "metadata": {
        "path": "src/auth/token.py",
        "language": "python",
        "chunk_index": 2
      }
    }
  ]
}
```

### POST /query/pr-context

Get context for PR review (with query decomposition).

```json
{
  "workspace": "my-org",
  "project": "my-repo",
  "branch": "main",
  "changed_files": ["src/auth/token.py", "src/auth/service.py"],
  "diff_snippets": ["def refresh_token(self):"],
  "pr_title": "Fix token refresh bug",
  "pr_description": "Resolves issue #123",
  "top_k": 15,
  "enable_priority_reranking": true,
  "min_relevance_score": 0.7
}
```

**Response:**
```json
{
  "relevant_code": [
    {
      "text": "class TokenValidator:\n    ...",
      "score": 0.95,
      "metadata": {
        "path": "src/auth/validator.py",
        "_priority": "HIGH"
      }
    }
  ],
  "related_files": ["src/auth/validator.py", "src/auth/jwt.py"],
  "changed_files": ["src/auth/token.py", "src/auth/service.py"]
}
```

### GET /health

Health check.

```json
{
  "status": "healthy",
  "qdrant": "connected",
  "collections": 5
}
```

---

## Configuration

### Environment Variables

```bash
# Qdrant
QDRANT_URL="http://qdrant:6333"
QDRANT_COLLECTION_PREFIX="codecrow"

# Embedding
OPENROUTER_API_KEY="sk-or-..."
OPENROUTER_BASE_URL="https://openrouter.ai/api/v1"
OPENROUTER_MODEL="openai/text-embedding-3-small"
EMBEDDING_DIM=1536

# Chunking
CHUNK_SIZE=800
CHUNK_OVERLAP=200
TEXT_CHUNK_SIZE=1000
TEXT_CHUNK_OVERLAP=200

# Limits
MAX_FILE_SIZE_BYTES=1048576  # 1MB
MAX_CHUNKS_PER_INDEX=10000
MAX_FILES_PER_INDEX=1000

# Server
HOST=0.0.0.0
PORT=8001
```

### RAGConfig Model

```python
@dataclass
class RAGConfig:
    # Qdrant
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection_prefix: str = "codecrow"
    
    # Embedding
    openrouter_api_key: str = ""
    openrouter_model: str = "openai/text-embedding-3-small"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    embedding_dim: int = 1536
    
    # Chunking
    chunk_size: int = 800
    chunk_overlap: int = 200
    text_chunk_size: int = 1000
    text_chunk_overlap: int = 200
    
    # Limits
    max_file_size_bytes: int = 1_048_576
    max_chunks_per_index: int = 10_000
    max_files_per_index: int = 1_000
    
    # Exclude patterns
    excluded_patterns: List[str] = field(default_factory=list)
```

---

## Deployment

### Docker

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
ENV PYTHONPATH=/app/src

CMD ["python", "main.py"]
```

### Docker Compose

```yaml
services:
  rag-pipeline:
    build: ./python-ecosystem/rag-pipeline
    ports:
      - "8001:8001"
    environment:
      - QDRANT_URL=http://qdrant:6333
      - OPENROUTER_API_KEY=${OPENROUTER_API_KEY}
    depends_on:
      - qdrant

  qdrant:
    image: qdrant/qdrant:latest
    ports:
      - "6333:6333"
    volumes:
      - qdrant_data:/qdrant/storage
```

### Health Checks

```bash
# RAG Pipeline health
curl http://localhost:8001/health

# Qdrant health
curl http://localhost:6333/health
```

---

## Related Documentation

- [MCP Client Documentation](../mcp-client/README.MD)
- [Integration Guide](docs/INTEGRATION_GUIDE.md)
- [Deployment Guide](docs/DEPLOYMENT.md)
- [MCP Scaling Strategy](../../docs/architecture/mcp-scaling-strategy.md)

