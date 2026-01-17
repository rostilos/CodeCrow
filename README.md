# CodeCrow

**CodeCrow** is an enterprise-grade, AI-powered code review platform designed to automate the security and quality analysis of your pull requests and branches. By combining large language models with a Retrieval-Augmented Generation (RAG) pipeline, CodeCrow understands your entire codebase, providing deep, context-aware feedback directly in your VCS platform.

## Capabilities by Platform

CodeCrow supports multiple version control systems with varying levels of integration. Below is the current feature matrix:

| Feature                | Bitbucket | GitHub | GitLab |
|:-----------------------| :---: | :---: | :---: |
| PR Analysis            | + | + | + |
| Branch Analysis        | + | + | + |
| Task Context Retrieval | - | - | - |
| /ask                   | + | + | + |
| /analyze               | + | + | + |
| /summarize             | + | + | + |
| Continuous Analysis    | + | + | + |
| RAG Pipeline           | + | + | + |

## Key Features

- **Context-Aware Reviews**: Powered by a custom RAG (Retrieval-Augmented Generation) pipeline using Qdrant vector storage.
- **Incremental Analysis**: Only scans changed code to keep feedback fast and cost-efficient.
- **Multi-Tenant Architecture**: Securely manage multiple teams and projects from a single dashboard.
- **Interactive Commands**: Command CodeCrow directly from PR comments using `/ask`, `/analyze`, and `/summarize`.

## Documentation

For full setup guides, architectural deep-dives, and API reference, please visit our documentation portal:

👉 [**codecrow.cloud/docs**](https://codecrow.cloud/docs)

---

## Architecture at a glance

High level components:
- **Web frontend** (`frontend/`) – React-based UI for workspaces, projects, dashboards, and issue views.
- **Web server / API** (`java-ecosystem/services/web-server/`) – main backend API, auth, workspaces/projects, and orchestration.
- **Pipeline agent** (`java-ecosystem/services/pipeline-agent/`) – receives VCS webhooks, fetches repo/PR data, and coordinates analysis.
- **MCP client** (`python-ecosystem/mcp-client/`) – executes analyzers and calls LLMs using the Model Context Protocol.
- **RAG pipeline** (`rag-pipeline/`) – indexes code and review artifacts into **Qdrant** for semantic search.

---

## Contributing

Contributions are welcome. Please see our [Development Guide](https://codecrow.cloud/docs/dev/development) for more information.
