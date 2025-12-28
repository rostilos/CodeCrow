# CodeCrow MCP Client

A sophisticated Python application for AI-powered code review using MCP (Model Context Protocol) servers with advanced RAG integration and Lost-in-the-Middle protection.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [RAG Integration Flow](#rag-integration-flow)
- [Lost-in-the-Middle Protection](#lost-in-the-middle-protection)
- [Smart Chunking System](#smart-chunking-system)
- [Context Building Pipeline](#context-building-pipeline)
- [Token Budget Management](#token-budget-management)
- [Caching & Optimization](#caching--optimization)
- [Installation](#installation)
- [Configuration](#configuration)
- [API Reference](#api-reference)

---

## Overview

CodeCrow MCP Client is the core component responsible for:
- Receiving code review requests from the Pipeline Agent
- Extracting and analyzing Pull Request diffs
- Integrating RAG context for enhanced code understanding
- Applying Lost-in-the-Middle protection for large PRs
- Orchestrating LLM-based code review with MCP servers

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         CodeCrow MCP Client                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐  │
│  │   FastAPI   │───▶│   Review    │───▶│   Context   │───▶│     LLM     │  │
│  │   Server    │    │   Service   │    │   Builder   │    │   (MCP)     │  │
│  └─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘  │
│         │                 │                   │                   │         │
│         │                 │                   │                   │         │
│         ▼                 ▼                   ▼                   ▼         │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐  │
│  │    RAG      │    │    File     │    │   Smart     │    │   Response  │  │
│  │   Client    │    │ Classifier  │    │  Chunker    │    │   Parser    │  │
│  └─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Architecture

### Project Structure

```
mcp-client/
├── main.py                    # Application entry point
├── model/
│   └── models.py              # Pydantic data models
├── llm/
│   └── llm_factory.py         # LLM provider factory
├── server/
│   ├── mcp_config.py          # MCP server configuration
│   └── web_server.py          # FastAPI HTTP server
├── service/
│   ├── review_service.py      # Core review orchestration
│   ├── rag_client.py          # RAG Pipeline client
│   └── llm_reranker.py        # LLM-based result reranking
├── utils/
│   ├── prompt_builder.py      # Prompt generation with L-i-M protection
│   ├── response_parser.py     # JSON response extraction
│   ├── context_builder.py     # Structured context assembly
│   ├── file_classifier.py     # Priority-based file classification
│   └── diff_parser.py         # Unified diff parsing
└── requirements.txt
```

### Core Components

| Component | Responsibility |
|-----------|---------------|
| `ReviewService` | Orchestrates the entire review pipeline |
| `RagClient` | Communicates with RAG Pipeline for context |
| `ContextBuilder` | Assembles prioritized context with token budgets |
| `FileClassifier` | Classifies files by security/business priority |
| `SmartChunker` | Intelligent code chunking preserving boundaries |
| `LLMReranker` | LLM-based reranking for large PRs |
| `PromptBuilder` | Generates prompts with Lost-in-Middle protection |

---

## RAG Integration Flow

The MCP Client integrates with the RAG Pipeline to provide semantic code context during reviews.

```
┌────────────────────────────────────────────────────────────────────────────┐
│                        RAG Integration Flow                                │
└────────────────────────────────────────────────────────────────────────────┘

     PR Request                                              
         │                                                   
         ▼                                                   
┌─────────────────┐                                          
│  1. Extract PR  │     Changed Files: [auth.py, user.py]    
│     Metadata    │     PR Title: "Fix auth token refresh"   
└────────┬────────┘                                          
         │                                                   
         ▼                                                   
┌─────────────────┐      ┌──────────────────────────────────┐
│  2. Query RAG   │─────▶│  RAG Pipeline (Qdrant)           │
│     Pipeline    │      │  - Semantic search               │
│                 │◀─────│  - Query decomposition           │
└────────┬────────┘      │  - Priority reranking            │
         │               └──────────────────────────────────┘
         ▼                                                   
┌─────────────────┐                                          
│  3. Rerank &    │     Apply file priority boosting        
│     Filter      │     Filter by relevance threshold       
└────────┬────────┘     Deduplicate similar chunks          
         │                                                   
         ▼                                                   
┌─────────────────┐                                          
│  4. Build       │     HIGH Priority (30% tokens)          
│     Context     │     MEDIUM Priority (40% tokens)        
│                 │     LOW Priority (20% tokens)           
└────────┬────────┘     RAG Context (10% tokens)            
         │                                                   
         ▼                                                   
┌─────────────────┐                                          
│  5. Generate    │     System Prompt + L-i-M Instructions  
│     Prompt      │     + Structured Context                
└────────┬────────┘                                          
         │                                                   
         ▼                                                   
    LLM Review                                               
```

### RAG Query Parameters

```python
# Example RAG context request
await rag_client.get_pr_context(
    workspace="my-org",
    project="my-repo",
    branch="main",
    changed_files=["src/auth/token.py", "src/user/profile.py"],
    diff_snippets=["def refresh_token(self):", "class UserProfile:"],
    pr_title="Fix auth token refresh bug",
    pr_description="Resolves issue with expired tokens...",
    top_k=15,                          # Max RAG chunks to retrieve
    enable_priority_reranking=True,    # Boost high-priority files
    min_relevance_score=0.7            # Filter low-relevance results
)
```

---

## Lost-in-the-Middle Protection

Large Language Models often lose focus on content in the middle of long prompts. CodeCrow implements several strategies to combat this.

### The Problem

```
┌────────────────────────────────────────────────────────────┐
│           LLM Attention Distribution (Typical)             │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  Attention                                                 │
│      ▲                                                     │
│  100%│  ████                                    ████       │
│      │  ████                                    ████       │
│   75%│  ████                                    ████       │
│      │  ████    ░░░░░░░░░░░░░░░░░░░░░░░░░░     ████       │
│   50%│  ████    ░░░░░░░░░░░░░░░░░░░░░░░░░░     ████       │
│      │  ████    ░░░░░░░░░░░░░░░░░░░░░░░░░░     ████       │
│   25%│  ████    ░░░░░░░░░░░░░░░░░░░░░░░░░░     ████       │
│      │  ████    ░░░░░░░░░░░░░░░░░░░░░░░░░░     ████       │
│    0%└──────────────────────────────────────────────▶      │
│         START   ◀── LOST ZONE ──▶              END        │
│                                                            │
│  Problem: Critical code in middle gets less attention!     │
└────────────────────────────────────────────────────────────┘
```

### Solution: Priority-Based Structuring

```
┌────────────────────────────────────────────────────────────┐
│           CodeCrow Context Structure                       │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  ████ SYSTEM PROMPT + L-i-M INSTRUCTIONS ████        │  │
│  │  "Focus on ALL sections, especially HIGH PRIORITY"   │  │
│  └──────────────────────────────────────────────────────┘  │
│                          │                                 │
│                          ▼                                 │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  ████ HIGH PRIORITY (30% tokens) ████                │  │
│  │  Security, Auth, Core Services                       │  │
│  │  >>> EXPLICIT MARKERS FOR LLM ATTENTION <<<          │  │
│  └──────────────────────────────────────────────────────┘  │
│                          │                                 │
│                          ▼                                 │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  ░░░░ MEDIUM PRIORITY (40% tokens) ░░░░              │  │
│  │  Models, DTOs, Components                            │  │
│  │  (Larger but lower risk)                             │  │
│  └──────────────────────────────────────────────────────┘  │
│                          │                                 │
│                          ▼                                 │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  ░░░░ LOW PRIORITY (20% tokens) ░░░░                 │  │
│  │  Tests, Config, Docs                                 │  │
│  └──────────────────────────────────────────────────────┘  │
│                          │                                 │
│                          ▼                                 │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  ████ RAG CONTEXT (10% tokens) ████                  │  │
│  │  Relevant code from repository                       │  │
│  │  >>> END WITH IMPORTANT CONTEXT <<<                  │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

### L-i-M Instructions in Prompt

```python
LOST_IN_MIDDLE_INSTRUCTIONS = """
## CRITICAL: Context Processing Instructions

This code review contains STRUCTURED CONTEXT organized by priority.
You MUST process ALL sections with equal attention:

1. **HIGH PRIORITY** (marked with ===HIGH PRIORITY===)
   - Security-critical files, authentication, core services
   - Analyze FIRST and MOST THOROUGHLY
   
2. **MEDIUM PRIORITY** (marked with ===MEDIUM PRIORITY===)
   - Dependencies, models, utilities
   - Check for consistency and proper patterns
   
3. **LOW PRIORITY** (marked with ===LOW PRIORITY===)
   - Tests, configurations
   - Quick scan for obvious issues
   
4. **RAG CONTEXT** (marked with ===RAG===)
   - Additional repository context
   - Use to understand patterns and dependencies

⚠️ DO NOT skip middle sections. Each section contains unique issues.
"""
```

---

## Smart Chunking System

When files exceed token limits, the SmartChunker preserves logical code boundaries.

### Supported Languages

| Language | File Extensions | Boundary Detection |
|----------|----------------|-------------------|
| Python | `.py`, `.pyx`, `.pyi` | `def`, `class`, `async def`, decorators |
| TypeScript | `.ts`, `.tsx`, `.mts`, `.cts` | `function`, `class`, `interface`, `type`, `enum`, methods |
| JavaScript | `.js`, `.jsx`, `.mjs`, `.cjs` | `function`, `class`, arrow functions, methods |
| Java | `.java` | `class`, `interface`, `enum`, methods, annotations |
| Kotlin | `.kt`, `.kts` | `class`, `object`, `fun`, `interface`, `companion` |
| Go | `.go` | `func`, `type`, `struct`, `interface` |
| PHP | `.php`, `.phtml` | `function`, `class`, `interface`, `trait`, `namespace` |
| C# | `.cs` | `class`, `interface`, `struct`, `enum`, `record`, methods |
| C++ | `.cpp`, `.cc`, `.h`, `.hpp` | `class`, `struct`, `namespace`, `template`, functions |
| Swift | `.swift` | `class`, `struct`, `enum`, `protocol`, `extension`, `func` |

### Chunking Algorithm

```
┌────────────────────────────────────────────────────────────┐
│                  Smart Chunking Flow                       │
└────────────────────────────────────────────────────────────┘

Input: Large file (>max_tokens)
         │
         ▼
┌─────────────────┐
│ 1. Detect       │     .py → Python patterns
│    Language     │     .ts → TypeScript patterns
└────────┬────────┘     .java → Java patterns
         │
         ▼
┌─────────────────┐
│ 2. Extract      │     import os
│    Imports      │     from typing import List
└────────┬────────┘     (Preserved in each chunk)
         │
         ▼
┌─────────────────┐     Line 1:   class UserService:
│ 3. Find         │     Line 45:  def authenticate():
│    Boundaries   │     Line 120: def refresh_token():
└────────┬────────┘     Line 200: class TokenValidator:
         │
         ▼
┌─────────────────┐     Chunk 1: imports + UserService
│ 4. Build        │     Chunk 2: imports + authenticate()
│    Chunks       │     Chunk 3: imports + refresh_token()
└────────┬────────┘     Chunk 4: imports + TokenValidator
         │
         ▼
Output: List[str] chunks with preserved structure
```

### Python Example

```python
# Original file: auth_service.py (5000 tokens)

# SmartChunker detects boundaries:
# - Line 1: imports
# - Line 20: class AuthService
# - Line 150: def validate_token()
# - Line 250: def refresh_token()
# - Line 350: class TokenStore

# Result: 3 chunks, each < 2000 tokens
# Each chunk includes imports header
```

### Diff Chunking

For large diffs, hunks are kept together:

```diff
# Original diff with 10 hunks

@@ -10,7 +10,7 @@ class Auth:      ← Hunk 1
@@ -50,12 +50,15 @@ def login():    ← Hunk 2
...
@@ -500,8 +510,8 @@ def logout():   ← Hunk 10

# SmartChunker groups hunks by token budget:
# Chunk 1: File header + Hunks 1-4
# Chunk 2: File header + Hunks 5-7
# Chunk 3: File header + Hunks 8-10
```

---

## Context Building Pipeline

### File Classification

Files are classified by business/security importance:

```
┌────────────────────────────────────────────────────────────┐
│                  File Priority Classification              │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ HIGH PRIORITY (Security Critical)                   │   │
│  │                                                     │   │
│  │ Patterns:                                           │   │
│  │ • auth/, security/, permission/                     │   │
│  │ • *Service.*, *Controller.*, *Handler.*            │   │
│  │ • jwt, token, session, crypto                       │   │
│  │ • password, credential, secret                      │   │
│  │ • middleware, interceptor, guard                    │   │
│  │                                                     │   │
│  │ Token Budget: 30% of total                          │   │
│  └─────────────────────────────────────────────────────┘   │
│                          │                                 │
│                          ▼                                 │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ MEDIUM PRIORITY (Business Logic)                    │   │
│  │                                                     │   │
│  │ Patterns:                                           │   │
│  │ • model/, entity/, dto/, schema/                    │   │
│  │ • repository/, dao/, store/                         │   │
│  │ • component/, hook/, util/, helper/                 │   │
│  │ • client/, integration/, api/                       │   │
│  │                                                     │   │
│  │ Token Budget: 40% of total                          │   │
│  └─────────────────────────────────────────────────────┘   │
│                          │                                 │
│                          ▼                                 │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ LOW PRIORITY (Supporting Code)                      │   │
│  │                                                     │   │
│  │ Patterns:                                           │   │
│  │ • test/, spec/, __tests__/                          │   │
│  │ • *.test.*, *.spec.*                                │   │
│  │ • config/, .config, settings                        │   │
│  │ • mock/, fixture/, stub/                            │   │
│  │                                                     │   │
│  │ Token Budget: 20% of total                          │   │
│  └─────────────────────────────────────────────────────┘   │
│                          │                                 │
│                          ▼                                 │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ SKIP (Auto-generated/Binary)                        │   │
│  │                                                     │   │
│  │ Patterns:                                           │   │
│  │ • *.min.js, *.bundle.*, *.map                       │   │
│  │ • package-lock.json, yarn.lock                      │   │
│  │ • *.pb.go, *_generated.*, *.g.dart                  │   │
│  │ • migrations/, dist/, build/                        │   │
│  │                                                     │   │
│  │ Token Budget: 0% (excluded)                         │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

### Context Assembly

```python
# ContextBuilder assembles final prompt context
builder = ContextBuilder(budget=ContextBudget.for_model("claude-3-sonnet"))

structured_context = builder.build_structured_context(
    pr_metadata={"title": "Fix auth bug", "description": "..."},
    diff_content={
        "src/auth/service.py": "...",
        "src/models/user.py": "...",
        "tests/test_auth.py": "..."
    },
    rag_context=rag_results,
    file_paths=["src/auth/service.py", "src/models/user.py", "tests/test_auth.py"]
)

# Output structure:
# === HIGH PRIORITY: Core Business Logic ===
# (security, auth, core services)
# Files: 1 | ~3500 tokens
# 
# ### src/auth/service.py
# Category: auth_service | Importance: 0.95
# ```
# [diff content]
# ```
#
# === END HIGH PRIORITY ===
#
# === MEDIUM PRIORITY: Dependencies & Shared Utils ===
# ...
```

---

## Token Budget Management

### Model-Specific Limits

The system automatically adjusts token budgets based on the LLM model:

```python
MODEL_CONTEXT_LIMITS = {
    # OpenAI Models
    "gpt-4-turbo": 90000,
    "gpt-4": 6000,
    "gpt-4o": 128000,
    
    # Anthropic Models
    "claude-3-opus": 140000,
    "claude-3-sonnet": 140000,
    "claude-3-5-sonnet": 200000,
    
    # Google Models
    "gemini-pro-1.5": 700000,
    "gemini-2.5-pro": 2000000,
    
    # Meta Models
    "llama-4-405b": 128000,
    "llama-4-scout": 1000000,
    
    # DeepSeek Models
    "deepseek-v3": 128000,
    
    "default": 200000
}
```

### Budget Distribution

```
┌────────────────────────────────────────────────────────────┐
│           Token Budget Distribution (Claude-3)             │
│                    Total: 140,000 tokens                   │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  ┌────────────────────────────────────────────────────┐    │
│  │  HIGH PRIORITY                          30%        │    │
│  │  ████████████████████████████           42,000     │    │
│  └────────────────────────────────────────────────────┘    │
│                                                            │
│  ┌────────────────────────────────────────────────────┐    │
│  │  MEDIUM PRIORITY                        40%        │    │
│  │  ████████████████████████████████████   56,000     │    │
│  └────────────────────────────────────────────────────┘    │
│                                                            │
│  ┌────────────────────────────────────────────────────┐    │
│  │  LOW PRIORITY                           20%        │    │
│  │  ████████████████████               28,000         │    │
│  └────────────────────────────────────────────────────┘    │
│                                                            │
│  ┌────────────────────────────────────────────────────┐    │
│  │  RAG CONTEXT                            10%        │    │
│  │  ██████████                          14,000        │    │
│  └────────────────────────────────────────────────────┘    │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

### Dynamic Adjustment

```python
# Budget automatically scales with model
budget_gpt4 = ContextBudget.for_model("gpt-4")  # 6,000 tokens total
budget_claude = ContextBudget.for_model("claude-3-sonnet")  # 140,000 tokens total
budget_gemini = ContextBudget.for_model("gemini-2.5-pro")  # 2,000,000 tokens total

# Same percentage distribution, different absolute values
print(budget_gpt4.high_priority_tokens)   # 1,800
print(budget_claude.high_priority_tokens)  # 42,000
print(budget_gemini.high_priority_tokens)  # 600,000
```

---

## Caching & Optimization

### RAG Cache

In-memory caching reduces redundant RAG queries:

```
┌────────────────────────────────────────────────────────────┐
│                     RAG Cache Flow                         │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  Request                                                   │
│     │                                                      │
│     ▼                                                      │
│  ┌─────────────────┐                                       │
│  │  Generate Key   │     MD5(workspace:project:branch:     │
│  │                 │          files:title:desc)            │
│  └────────┬────────┘                                       │
│           │                                                │
│           ▼                                                │
│  ┌─────────────────┐                                       │
│  │  Check Cache    │────── HIT ──────▶ Return cached      │
│  │                 │                     (skip RAG query)  │
│  └────────┬────────┘                                       │
│           │                                                │
│          MISS                                              │
│           │                                                │
│           ▼                                                │
│  ┌─────────────────┐                                       │
│  │  Query RAG      │                                       │
│  │  Pipeline       │                                       │
│  └────────┬────────┘                                       │
│           │                                                │
│           ▼                                                │
│  ┌─────────────────┐                                       │
│  │  Store in       │     TTL: 5 minutes                   │
│  │  Cache          │     Max entries: 100                 │
│  └────────┬────────┘                                       │
│           │                                                │
│           ▼                                                │
│       Return result                                        │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

### Cache Configuration

```python
class RAGCache:
    DEFAULT_TTL_SECONDS = 300   # 5 minutes
    MAX_CACHE_SIZE = 100        # Max entries
    
    # Auto-eviction of oldest 10% when full
    # Invalidation by workspace/project/branch
```

### RAG Metrics

Performance tracking for RAG operations:

```python
@dataclass
class RAGMetrics:
    query_count: int              # Number of RAG queries
    total_results: int            # Raw results before filtering
    filtered_results: int         # Results after threshold filter
    high_priority_hits: int       # Results from high-priority files
    medium_priority_hits: int     # Results from medium-priority files
    low_priority_hits: int        # Results from low-priority files
    avg_relevance_score: float    # Average relevance (0-1)
    min_relevance_score: float
    max_relevance_score: float
    processing_time_ms: float     # Total RAG processing time
    reranking_applied: bool       # Whether reranking was used
    cache_hit: bool               # Whether result was from cache
```

---

## Installation

### Requirements

- Python 3.10+
- RAG Pipeline running (optional but recommended)
- MCP Server JAR file

### Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.sample .env
# Edit .env with your settings
```

---

## Configuration

### Environment Variables

```bash
# Server Configuration
AI_CLIENT_HOST="0.0.0.0"
AI_CLIENT_PORT="8000"

# MCP Server
MCP_SERVER_JAR="/path/to/mcp-server.jar"

# LLM Provider
OPENROUTER_API_KEY="your-api-key"

# RAG Integration
RAG_ENABLED="true"
RAG_API_URL="http://rag-pipeline:8001"
```

### Runtime Configuration

```python
# Custom context budget
budget = ContextBudget(
    total_tokens=100000,
    high_priority_pct=0.35,    # 35% for critical files
    medium_priority_pct=0.35,  # 35% for dependencies
    low_priority_pct=0.20,     # 20% for tests
    rag_context_pct=0.10       # 10% for RAG context
)
```

---

## API Reference

### POST /review

Submit a code review request.

**Request:**
```json
{
  "projectId": 123,
  "projectWorkspace": "workspace",
  "projectRepoSlug": "repo-name",
  "aiProvider": "openrouter",
  "aiModel": "anthropic/claude-3-5-sonnet",
  "aiApiKey": "your-api-key",
  "pullRequestId": 456,
  "ragEnabled": true,
  "ragTopK": 15,
  "ragMinScore": 0.7
}
```

**Response:**
```json
{
  "result": {
    "comment": "Review summary with findings...",
    "issues": {
      "0": {
        "severity": "HIGH",
        "file": "src/auth/service.py",
        "line": "42",
        "reason": "SQL injection vulnerability in user query",
        "suggestedFix": "Use parameterized queries..."
      }
    }
  },
  "metrics": {
    "rag_query_count": 3,
    "rag_results": 12,
    "rag_cache_hit": false,
    "processing_time_ms": 1250
  },
  "error": null
}
```

### GET /health

Health check endpoint.

**Response:**
```json
{
  "status": "healthy",
  "rag_enabled": true,
  "rag_healthy": true
}
```

---

## Related Documentation

- [RAG Pipeline Documentation](../rag-pipeline/README.md)
- [MCP Scaling Strategy](../../docs/architecture/mcp-scaling-strategy.md)
- [Integration Guide](../rag-pipeline/docs/INTEGRATION_GUIDE.md)