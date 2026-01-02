# AST-based Code Chunking

This document describes the AST-based code chunking feature in the RAG pipeline.

## Overview

The `ASTCodeSplitter` uses tree-sitter via LangChain's `LanguageParser` to parse code into semantic units (classes, functions, methods) rather than arbitrary character-based chunks.

### Benefits over Traditional Chunking

| Feature | Traditional (Regex) | AST-based |
|---------|---------------------|-----------|
| Boundary Detection | Line-based, may split mid-function | Accurate function/class boundaries |
| Language Support | Limited patterns | 15+ languages via tree-sitter |
| Metadata | Basic (file path, line numbers) | Rich (semantic names, docstrings, signatures) |
| Context Quality | May include partial code | Complete semantic units |

## Architecture

```
Document → ASTCodeSplitter → TextNodes (with metadata)
                ↓
         ┌──────────────────┐
         │ Language Check   │
         └────────┬─────────┘
                  │
         ┌────────▼─────────┐
         │ tree-sitter AST  │  (for supported languages)
         │    parsing       │
         └────────┬─────────┘
                  │
         ┌────────▼─────────┐
         │ Extract semantic │
         │ units (classes,  │
         │ functions)       │
         └────────┬─────────┘
                  │
         ┌────────▼─────────┐
         │ Oversized chunk? │──Yes──→ RecursiveCharacterTextSplitter
         └────────┬─────────┘
                  │No
         ┌────────▼─────────┐
         │ Enrich metadata  │
         │ (names, docs,    │
         │ signatures)      │
         └────────┬─────────┘
                  │
         ┌────────▼─────────┐
         │ Create TextNode  │
         └──────────────────┘
```

## Supported Languages

AST parsing is supported for:

- **Python** (.py, .pyw, .pyi)
- **Java** (.java)
- **JavaScript** (.js, .jsx, .mjs, .cjs)
- **TypeScript** (.ts, .tsx)
- **Go** (.go)
- **Rust** (.rs)
- **C/C++** (.c, .h, .cpp, .cc, .hpp)
- **C#** (.cs)
- **Kotlin** (.kt, .kts)
- **PHP** (.php)
- **Ruby** (.rb)
- **Scala** (.scala)
- **Swift** (.swift)
- **Lua** (.lua)
- **Perl** (.pl, .pm)
- **Haskell** (.hs)
- **COBOL** (.cob, .cbl)

## Content Types

Each chunk has a `content_type` metadata field:

| Content Type | Description | RAG Boost |
|--------------|-------------|-----------|
| `functions_classes` | Full function/class definitions | 1.2x |
| `simplified_code` | Remaining code with placeholders | 0.7x |
| `oversized_split` | Large chunks split by character | 0.95x |
| `fallback` | Non-AST parsed content | 1.0x |

## Metadata Extraction

For each chunk, the following metadata is extracted:

```python
{
    # Standard fields
    'path': 'src/service/UserService.java',
    'language': 'java',
    'chunk_index': 0,
    'total_chunks': 5,
    'start_line': 15,
    'end_line': 45,
    
    # AST-specific fields
    'content_type': 'functions_classes',
    'semantic_names': ['UserService', 'createUser', 'getUserById'],
    'primary_name': 'UserService',
    'signature': 'public class UserService',
    'docstring': 'Service class for user management operations.'
}
```

## Configuration

### Environment Variables

```bash
# Enable/disable AST splitter (default: true)
RAG_USE_AST_SPLITTER=true
```

### Splitter Parameters

```python
ASTCodeSplitter(
    max_chunk_size=2000,      # Max chars per chunk
    min_chunk_size=100,       # Min chars for valid chunk
    chunk_overlap=200,        # Overlap for oversized splits
    parser_threshold=10       # Min lines for AST parsing
)
```

## RAG Retrieval Boost

During retrieval, chunks are boosted based on:

1. **File Path Priority** - Service/controller files get 1.3x boost
2. **Content Type** - `functions_classes` get 1.2x boost
3. **Semantic Names** - Chunks with extracted names get 1.1x boost
4. **Docstrings** - Chunks with docstrings get 1.05x boost

Example combined boost:
```
UserService.java (service file) × functions_classes × has_names × has_docstring
= 1.3 × 1.2 × 1.1 × 1.05 = 1.8x boost
```

## Usage Example

```python
from rag_pipeline.core import ASTCodeSplitter

# Initialize splitter
splitter = ASTCodeSplitter(
    max_chunk_size=2000,
    parser_threshold=10
)

# Split documents
nodes = splitter.split_documents(documents)

# Access enriched metadata
for node in nodes:
    print(f"Path: {node.metadata['path']}")
    print(f"Type: {node.metadata['content_type']}")
    print(f"Names: {node.metadata.get('semantic_names', [])}")
    print(f"Signature: {node.metadata.get('signature', 'N/A')}")
```

## Fallback Behavior

When AST parsing fails or is not applicable:

1. **Unsupported language** → Uses `RecursiveCharacterTextSplitter` with default separators
2. **Small files** (< `parser_threshold` lines) → Uses fallback splitter
3. **Parse errors** → Logs warning and uses fallback
4. **Missing tree-sitter** → Falls back to regex-based `SemanticCodeSplitter`

## Dependencies

```
langchain-community>=0.3.0
langchain-text-splitters>=0.3.0
tree-sitter>=0.21.0
tree-sitter-languages>=1.10.0
```
