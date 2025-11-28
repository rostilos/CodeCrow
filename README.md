# CodeCrow

Automated, AI-powered code review for Bitbucket Cloud with incremental branch analysis, RAG-based context, and a multi-team web platform.

## Overview

CodeCrow connects to Bitbucket Cloud and automatically reviews pull requests and branches. It:
- runs branch-incremental analysis (only changed branches/PRs, not full-repo rescans)
- uses a dedicated RAG pipeline with Qdrant vector storage for semantic code search and PR context
- provides a web platform for multiple teams with secure workspaces and projects
- reports issues directly in Bitbucket (inline PR comments and summaries)
- surfaces line-level issues in IDEs (e.g. VS Code) using the same analysis results

For full documentation, see [`docs/README.md`](docs/README.md).

---

## Architecture at a glance

High level components:
- **Web frontend** (`frontend/`) – React-based UI for workspaces, projects, dashboards, and issue views.
- **Web server / API** (`java-ecosystem/services/web-server/`) – main backend API, auth, workspaces/projects, and orchestration.
- **Pipeline agent** (`java-ecosystem/services/pipeline-agent/`) – receives Bitbucket webhooks, fetches repo/PR data, prepares archives, and coordinates analysis.
- **MCP client** (`python-ecosystem/mcp-client/`) – accepts archives from the pipeline agent, calls MCP servers/LLMs, and returns structured findings.
- **MCP servers** (`java-ecosystem/mcp-servers/`) – language- and VCS-aware analyzers exposed via MCP.
- **RAG pipeline** (`rag-pipeline/`) – indexes code and review artifacts into **Qdrant** for semantic search and PR context retrieval.
- **Shared libraries** (`java-ecosystem/libs/core`, `libs/security`, `libs/vcs-client`) – shared models, security, and Bitbucket Cloud client logic.
- **Deployment & tooling** (`deployment/`, `tools/`) – docker-compose setup, config samples, and helper scripts.

---

## Key features

- **Branch-incremental analysis**
  - Only analyzes changed branches and pull requests.
  - Reuses previous results to keep feedback fast and cost-efficient.

- **RAG-powered semantic context (Qdrant-backed)**
  - `rag-pipeline` builds embeddings for code and related artifacts.
  - Stores vectors in **Qdrant** for high-performance semantic search.
  - Provides relevant code/context snippets for reviews and explanations.

- **Multi-team web platform**
  - Separate, secure workspaces for different teams or organizations.
  - Projects live inside workspaces with their own tokens and configuration.
  - Dashboards for project health, analysis runs, and issue trends.

- **Deep Bitbucket Cloud integration**
  - Webhooks and API integration managed by the pipeline agent.
  - Inline PR comments and summaries pointing to specific lines and files.
  - Support for branch and PR lifecycles driven by Bitbucket events.

- **Modular analysis stack**
  - MCP client/servers can be extended with new analyzers.
  - Shared VCS and core libraries keep integrations consistent and testable.

---

## Quick start (Docker Compose)

### 1. Prerequisites

- Docker & Docker Compose installed and working
- Java 17+ and Maven (if you want to build modules locally)
- Bitbucket Cloud workspace/repository with admin access
- API keys/tokens for Bitbucket and your LLM provider (e.g. OpenRouter)

### 2. Configure services

From the repo root:

```bash
cp deployment/docker-compose-sample.yml deployment/docker-compose.yml
cp deployment/config/java-shared/application.properties.sample deployment/config/java-shared/application.properties
cp deployment/config/mcp-client/.env.sample deployment/config/mcp-client/.env
cp deployment/config/rag-pipeline/.env.sample deployment/config/rag-pipeline/.env
cp deployment/config/web-frontend/.env.sample deployment/config/web-frontend/.env
```

Edit the copied files to set:
- JWT secrets and encryption keys
- Bitbucket credentials and webhook configuration
- LLM / OpenRouter API keys

### 3. Build and start

From the repo root:

```bash
./tools/production-build.sh
```

This script:
- builds Java artifacts with Maven
- builds and starts Docker services via `docker compose`
- brings up web server, pipeline agent, MCP client/servers, RAG pipeline, Qdrant, and frontend (as defined in compose)

### 4. Access the platform

Once containers are healthy (see `deployment/docker-compose-sample.yml` for exact ports):
- Frontend UI: `http://localhost:8080`
- Web API: `http://localhost:8081`
- Pipeline agent API: `http://localhost:8082`
- RAG pipeline API: `http://localhost:8001`

### 5. Connect Bitbucket and run your first analysis

- Configure Bitbucket Cloud webhooks to point to the pipeline-agent endpoint.
- In the web UI, take the necessary steps: :
1. Create a workspace (or ask the owner or admin of an existing one to invite you).
2. Create a VSC connection (or reuse an existing one within the workspace).
3.  Create an AI connection (or reuse an existing one within the workspace).
4. Create a project within the workspace.
   4.1. Select the desired VCS connection and repository from the list of available ones.
   4.2. Select the desired AI connection.
   4.3. Set the project name.
5. After creating the project, follow the instructions provided on the platform for configuring repository variables and bitbucket pipelines.
- Open or update a pull request to trigger analysis.
- Review:
  - inline comments and reports in Bitbucket PRs
  - dashboards and issue lists in the web UI
  - line-level issues in your IDE (where supported)

---

## Documentation

Full docs live in the `docs/` folder:
- [`docs/01-overview.md`](docs/01-overview.md) – high-level overview
- [`docs/02-getting-started.md`](docs/02-getting-started.md) – end-to-end setup
- [`docs/03-architecture.md`](docs/03-architecture.md) – detailed architecture and data flow
- [`docs/05-configuration.md`](docs/05-configuration.md) – configuration reference
- [`docs/07-analysis-types.md`](docs/07-analysis-types.md) – available analysis types and checks
- [`docs/09-deployment.md`](docs/09-deployment.md) – deployment options and production notes
- [`docs/10-development.md`](docs/10-development.md) – development workflow and conventions

---

## Contributing

Contributions are welcome.

- See [`docs/10-development.md`](docs/10-development.md) for development setup and module-specific notes.
- See [`frontend/README.md`](frontend/README.md) for frontend development workflow.
- Please run the test suite (e.g. `mvn test` for Java modules) before opening pull requests.
