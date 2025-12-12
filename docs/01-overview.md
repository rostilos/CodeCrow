# Overview

## What is CodeCrow?

CodeCrow is an automated code review platform that supports multiple VCS providers including Bitbucket Cloud and GitHub. It analyzes code changes in pull requests and branches using AI-powered analysis through Model Context Protocol (MCP) servers, combined with Retrieval-Augmented Generation (RAG) for contextual understanding of your codebase.

## Supported Platforms

| Platform | OAuth App | Personal Token | Webhooks | PR Comments |
|----------|-----------|----------------|----------|-------------|
| Bitbucket Cloud | ✅ | ✅ | ✅ | ✅ |
| GitHub | ✅ | ✅ | ✅ | ✅ |
| Bitbucket Server | ❌ | ✅ | ✅ | ✅ |
| GitLab | 🚧 Coming soon | 🚧 | 🚧 | 🚧 |

## Key Features

- **Automated Code Analysis**: Analyzes pull requests and branch merges automatically via webhooks
- **Multi-Platform Support**: Connect Bitbucket Cloud, GitHub, or both simultaneously
- **AI-Powered Reviews**: Uses LLM models through OpenRouter for intelligent code analysis
- **Contextual Understanding**: RAG pipeline indexes your codebase for context-aware analysis
- **Issue Tracking**: Tracks issues across branches and pull requests, detects when issues are resolved
- **Multi-Repository Support**: Manage multiple workspaces and projects
- **Private Repository Access**: Custom MCP servers for secure VCS API access
- **Role-Based Access**: Workspace and project-level permissions

## System Components

### Java Ecosystem
- **codecrow-core**: Shared models, persistence layer, common services
- **codecrow-security**: Authentication, authorization, JWT handling
- **codecrow-vcs-client**: VCS platform API client (Bitbucket, GitHub)
- **pipeline-agent**: Analysis processing engine and API gateway
- **web-server**: Main backend REST API
- **bitbucket-mcp**: MCP servers for VCS integration (supports both Bitbucket and GitHub)

### Python Ecosystem
- **mcp-client**: Modified MCP client that generates prompts and communicates with AI
- **rag-pipeline**: Vector database indexing and semantic search

### Frontend
- React-based web interface with shadcn/ui components

### Infrastructure
- PostgreSQL for relational data
- Redis for sessions and caching
- Qdrant for vector storage
- Docker Compose orchestration

## Analysis Flow

1. **Webhook Trigger**: VCS platform sends webhook on PR creation/update or branch merge
2. **Pipeline Agent**: Receives webhook, fetches code and metadata
3. **RAG Indexing**: First-time branch analysis triggers full repository indexing
4. **MCP Client**: Generates analysis prompts with context from RAG and previous issues
5. **AI Analysis**: OpenRouter processes prompts using configured LLM
6. **Result Processing**: Pipeline agent stores results, updates issue status
7. **Web Interface**: Users view analysis results and manage projects

## Core Concepts

### Workspaces
Top-level organizational unit. Each workspace contains projects and has members with specific roles.

### Projects
Repository representation within a workspace. Projects are bound to VCS repositories and AI connections.

### Analysis Types
- **Branch Analysis**: Incremental analysis after PR merge, checks if existing issues are resolved
- **Pull Request Analysis**: Analyzes changed files when PR is created or updated

### Issues
Code problems detected by analysis. Tracked at branch level (BranchIssue) and PR level (CodeAnalysisIssue).

### RAG Integration
Repository code is indexed in Qdrant vector database. During analysis, relevant code context is retrieved to improve AI understanding.

## Technology Stack

**Backend**:
- Java 17, Spring Boot 3.2.5
- PostgreSQL 15
- Redis 7
- Maven

**Python Services**:
- FastAPI, Uvicorn
- LangChain, LlamaIndex
- Qdrant Client
- OpenAI SDK (OpenRouter compatible)

**Frontend**:
- React, TypeScript
- Vite
- shadcn/ui, Radix UI
- TanStack Query

**Infrastructure**:
- Docker, Docker Compose
- PostgreSQL, Redis, Qdrant

