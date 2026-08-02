# CodeCrow

**CodeCrow** is a self-hosted, bring-your-own-model code review platform for
GitHub, GitLab, and Bitbucket. It combines bounded multi-stage model review with
deterministic code evidence and optional Retrieval-Augmented Generation (RAG).

Statically assembled language, framework, and domain plugins add syntax,
repository-graph, planning, prompt, and validation context without adding model
calls. Projects that do not match a dedicated plugin continue through the generic
review and indexing fallbacks.

[Self-hosting documentation](https://codecrow.app/docs/self-host)

## Capabilities by Platform

CodeCrow supports multiple version control systems. The AI analysis engine is the same across all platforms — the differences are in how results are surfaced in each VCS.

<img width="2872" height="1584" alt="Screenshot_20260325_165201" src="https://github.com/user-attachments/assets/c991e827-f6f0-4514-bc11-e1a0cfc156b2" />
<img width="2872" height="1584" alt="Screenshot_20260325_165231" src="https://github.com/user-attachments/assets/14c47b88-fa38-43be-b9ef-6a4d02170219" />
<img width="1915" height="1077" alt="graph" src="https://github.com/user-attachments/assets/cbf6616f-3f82-403d-87ef-af7be6756264" />

### Analysis & Review

| Feature                                  | Bitbucket | GitHub | GitLab |
| :--------------------------------------- | :-------: | :----: | :----: |
| PR / MR Analysis                         |    ✅     |   ✅   |   ✅   |
| Branch Analysis (push)                   |    ✅     |   ✅   |   ✅   |
| Continuous Analysis                      |    ✅     |   ✅   |   ✅   |
| Incremental / Delta Diff                 |    ✅     |   ✅   |   ✅   |
| Immutable Commit-Pinned Review Input     |    ✅     |   ✅   |   ✅   |
| Optional RAG-Augmented Review            |    ✅     |   ✅   |   ✅   |
| Review with RAG Disabled                 |    ✅     |   ✅   |   ✅   |
| Deterministic Plugin Context             |    ✅     |   ✅   |   ✅   |
| Changed-Hunk Coverage and Evidence Gates |    ✅     |   ✅   |   ✅   |
| Cross-File Candidate Verification        |    ✅     |   ✅   |   ✅   |
| Full-Pipeline Prompt Dry Run             |    ✅     |   ✅   |   ✅   |
| Jira Task Context Review                 |    ✅     |   ✅   |   ✅   |

### PR / MR Comment Integration

| Feature                            |     Bitbucket     | GitHub | GitLab |
| :--------------------------------- | :---------------: | :----: | :----: |
| PR Summary Comment                 |        ✅         |   ✅   |   ✅   |
| Inline Diff Comments               | via Code Insights |   ✅   |   ✅   |
| Code Insights Report + Annotations |        ✅         |   —    |   —    |
| Check Runs                         |         —         |   ✅   |   —    |
| Threaded Comment Replies           |        ✅         |   —    |   ✅   |
| Placeholder While Analyzing        |        ✅         |   ✅   |   ✅   |

### Slash Commands (in PR comments)

| Command                       | Bitbucket | GitHub | GitLab |
| :---------------------------- | :-------: | :----: | :----: |
| `/codecrow ask <question>`    |    ✅     |   ✅   |   ✅   |
| `/codecrow analyze`           |    ✅     |   ✅   |   ✅   |
| `/codecrow review`            |    ✅     |   ✅   |   ✅   |
| `/codecrow summarize`         |    ✅     |   ✅   |   ✅   |
| `/codecrow qa-doc [TASK-KEY]` |    ✅     |   ✅   |   ✅   |

<img width="1574" height="1560" alt="demo-interactive-agent-gh-DlzQ03-N" src="https://github.com/user-attachments/assets/f9bf0712-17e5-4710-8dd5-c26b908998aa" />

<img width="1793" height="660" alt="Screenshot_20260325_165750" src="https://github.com/user-attachments/assets/5b36cbbf-5e14-4c8f-be67-e505a8c37898" />

### Dashboard & Issue Management

These features are platform-independent and available through the CodeCrow web UI.

| Feature                     | Description                                                                                           |
| :-------------------------- | :---------------------------------------------------------------------------------------------------- |
| Issue Tracker               | Per-branch and per-PR issue lists with severity, category, and status filters                         |
| Issue Lifecycle             | Automatic resolution tracking across analyses; manual resolve/reopen                                  |
| Source Context Viewer       | Full source code browser with inline issue annotations for every analyzed file                        |
| Quality Gates               | Configurable pass/fail thresholds per workspace                                                       |
| Custom Rules                | Per-project enforce/suppress rules with glob-based file patterns                                      |
| Analysis and RAG Scopes     | Per-project include/exclude scopes with synchronized analysis and indexing coverage                   |
| RAG Configuration           | Per-project RAG controls, branch indexing status, last activity, progress, and reindex actions        |
| Vector Storage Explorer     | Inspect semantic and PR chunks, architecture context, exact source, plugin state, and graph relations |
| Project Analytics           | Aggregated severity breakdown, analysis history, and branch health                                    |
| AI Model Selection          | OpenRouter, OpenAI, Anthropic, Google AI, Google Vertex AI, and OpenAI-compatible connections         |
| Workspace & Team Management | Roles (Owner, Admin, Member, Viewer), member invites, ownership transfer                              |
| Task Management (Jira)      | Connect Jira Cloud to link PRs with tasks for QA documentation, task-aware review, and comment sync   |
| QA Auto-Documentation       | AI-generated QA docs stored per PR in CodeCrow and posted as Jira comments                            |
| Two-Factor Authentication   | TOTP-based 2FA for sensitive operations                                                               |

### Setup Methods

| Method                   | Bitbucket Cloud |               GitHub                |                  GitLab                   |
| :----------------------- | :-------------: | :---------------------------------: | :---------------------------------------: |
| OAuth / App Installation |   ✅ (OAuth)    | ✅ (GitHub App with OAuth fallback) |           ✅ (GitLab.com only)            |
| Self-managed VCS         |        —          |                  —                  |    ✅ (personal or project access token)   |
| Manual Webhook           |       ✅        |                 ✅                  |                    ✅                     |
| CI Pipeline Action       |       ✅        |                  —                  |                     —                     |

Connection setup can recover retained GitHub and Bitbucket installations instead
of forcing a reinstall. Provider-specific cleanup removes or revokes owned app
installations and OAuth grants; a connection cannot be deleted while projects
still depend on it. A provider owner can approve a GitHub installation without a
CodeCrow account, after which the CodeCrow workspace administrator completes
project setup.

---

## Supported Languages

CodeCrow can send any reviewable text file to the configured model. That generic
review is model-dependent and is distinct from the deterministic support listed
below.

| Language tier                                                                                                            | Model Review | Changed-File Syntax Plugin | Semantic RAG Query | Exact Plugin Facts |
| :----------------------------------------------------------------------------------------------------------------------- | :----------: | :------------------------: | :----------------: | :----------------: |
| Java (`.java`)                                                                                                           |      ✅      |             ✅             |         ✅         |         ✅         |
| Python (`.py`, `.pyi`, `.pyw`)                                                                                           |      ✅      |             ✅             |         ✅         |         ✅         |
| JavaScript / JSX (`.js`, `.jsx`, `.mjs`, `.cjs`)                                                                         |      ✅      |             ✅             |         ✅         |         ✅         |
| TypeScript (`.ts`, `.mts`, `.cts`)                                                                                       |      ✅      |             ✅             |         ✅         |         ✅         |
| Go (`.go`)                                                                                                               |      ✅      |             ✅             |         ✅         |         ✅         |
| PHP / PHTML (`.php`, `.inc`, `.phtml`)                                                                                   |      ✅      |             ✅             |         ✅         |         ✅         |
| C# (`.cs`), Rust (`.rs`)                                                                                                 |      ✅      |             ✅             |         ✅         |         —          |
| TSX (`.tsx`)                                                                                                             |      ✅      |             ✅             |         ✅         |         —          |
| Bash / Shell, C, C++, CSS, Haskell, HTML, JSON, Ruby, Scala                                                              |      ✅      |             ✅             |      generic       |         —          |
| Kotlin, Swift, Lua, Perl, COBOL, Objective-C, SQL, R, SCSS, Vue/Svelte SFCs, YAML/TOML/XML, Markdown/RST, and other text |      ✅      |          fallback          |      generic       |         —          |

`generic` means language-aware or text chunking without a dedicated semantic RAG
query. TSX uses the TypeScript RAG parser/query compatibility path but is not
included in the TypeScript repository-fact session. C, C++, and Ruby ship RAG
parser packages but currently have no dedicated semantic query, so the table
reports their resulting generic chunk behavior rather than package availability.

Exact plugin facts are bounded, typed declarations and relationships. JavaScript,
TypeScript, and PHP maintain repository-scoped resolution sessions; Go, Java, and
Python currently contribute bounded per-file language facts. Neither path
replaces model review with a preset defect-rule engine.

### Framework and Domain Plugins

| Plugin         | Requires         | Deterministic Context                                                                                                                          |
| :------------- | :--------------- | :--------------------------------------------------------------------------------------------------------------------------------------------- |
| Spring         | Java             | Components, combined controller routes, dependency injection, beans, configuration, and Spring Data repository inheritance                     |
| FastAPI        | Python           | Applications, routers and route prefixes, HTTP/WebSocket routes, `Depends`, middleware, lifespan handlers, and exception handlers              |
| Magento 2      | PHP              | Module topology, DI and plugins, events, routes and ACLs, layouts, blocks, templates and themes, Web APIs, queues, schemas, and related source |
| Hyvä           | Magento 2        | ViewModel registry, layout/template, Alpine state/event, REST/Web API, DI, and bounded PHP call-chain relations                                |
| Data contracts | Language-neutral | Exact GraphQL, Protocol Buffers, JSON Schema, and explicit contract-path field declarations and references across languages                    |

Plugins are selected automatically from bounded facts at the pinned repository
revision. They are part of the local distribution, are not downloaded or
hot-loaded at runtime, and cannot call an LLM or embedding provider. The generic
Java and Python hosts remain the fallback when no plugin implementation matches.

## RAG and Incremental Indexing

RAG is optional per project. Disabling it skips indexing and retrieval while the
normal review pipeline continues.

| Capability          | Implemented Behavior                                                                                                                                       |
| :------------------ | :--------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Embeddings          | Deployment-level OpenRouter or Ollama configuration, separate from the project's review-model connection                                                   |
| Retrieval           | Qdrant semantic search combined with deterministic metadata and exact-path lookup                                                                          |
| Stored Context      | Semantic source chunks plus exact-source, architecture, graph, plugin snapshot, and repository-detection points                                            |
| Full Reindex        | Builds a pending collection generation and atomically swaps the project alias only after successful completion                                             |
| Resilient Writes    | Quarantines malformed files and exact rejected points while retaining valid content; systemic Qdrant failures still prevent activation                    |
| Incremental Reindex | Applies one pinned commit change set and replaces changed semantic chunks together with affected graph and state groups                                    |
| PR Context          | Uses an immutable, commit-pinned PR overlay so changed files do not retrieve stale base-branch copies                                                      |
| Compatibility Guard | Host, selection, descriptor, and implementation fingerprints are provenance only and never force a reindex or filter context; snapshot integrity and Qdrant vector shape remain enforced |
| Prompt Budget       | Plugin and RAG evidence share bounded context budgets; plugins cannot create an additional model stage                                                     |

The Vector Storage Explorer exposes the different point types and their
relationships. Deterministic architecture and state points intentionally use
stable content identities; they participate in graph invalidation and
incremental replacement rather than behaving as unrelated source files.

## Review Pipeline and Quality Controls

| Control                      | Behavior                                                                                                                             |
| :--------------------------- | :----------------------------------------------------------------------------------------------------------------------------------- |
| Immutable Input              | Acquires provider-authoritative base/head revisions, diff, and current source before model review                                    |
| Bounded Stages               | Plans the review, processes file/hunk batches, verifies candidates, reconciles cross-file evidence, and aggregates the final result  |
| Coverage Ledger              | Gives every reviewable changed hunk and review unit a terminal disposition                                                           |
| Evidence Gate                | Validates changed-line location, visible evidence, plugin proof decisions, suppression, and duplicate identity before publication    |
| Idempotent Evidence          | Persists deterministic execution, coverage, candidate, and finding identities for safe retry and lifecycle reconciliation            |
| Failure Semantics            | A failed or incomplete batch is not interpreted as a clean review; incomplete coverage blocks publication                            |
| Optional Model Extras        | Model-based RAG reranking and final semantic deduplication are disabled by default and add calls only when explicitly enabled        |
| Queue Liveness               | Capacity-first consumers renew locks and report heartbeats; timeout is based on inactivity rather than total healthy-review duration |
| Full-Pipeline Prompt Dry Run | Runs normal acquisition, enrichment, plugins, RAG, batching, and prompt assembly with a capture model instead of the review LLM      |
| Capture and Replay Tooling   | Provides opt-in prompt capture, disconnected fixtures, replay, paired evaluation, and publication-gate tooling for operators         |

Prompt dry-run is a deployment/operator switch, not a dashboard setting. It
suppresses analysis persistence and VCS mutations and writes artifacts under
`/app/logs/prompt-dry-runs`. It makes no review-model call, but an RAG-enabled run
can still call the configured embedding provider. See the
[testing guide](https://codecrow.app/docs/developer/testing) for the exact
configuration and audit procedure.

Real review-quality capture is a separate opt-in mode: it observes normal BYOK
calls and stores source-bearing prompts, responses, and evidence for allowlisted
projects. Treat those artifacts as sensitive and restrict access as described in
the [configuration guide](https://codecrow.app/docs/developer/configuration).

## Key Features

- **Evidence-Bound Reviews**: Multi-stage analysis with immutable inputs, changed-hunk coverage, candidate provenance, deterministic validation, and publication gates.
- **Context-Aware Reviews**: Optional semantic and deterministic RAG using Qdrant vector storage.
- **Plugin-Based Enrichment**: Local language, framework, and domain plugins add exact context while generic hosts remain available for every project.
- **Task-Aware PR Review**: When a project has a connected Jira task-management integration, PR analysis can include the linked task summary, description, status, priority, assignee, reporter, and URL. The setting `taskContextAnalysisEnabled` defaults to `true` and can be disabled per project through analysis settings.
- **Incremental Analysis and Indexing**: Reviews focus publication on changed hunks while retrieving related source; branch updates replace changed chunks and invalidated graph/state groups.
- **Multi-Tenant Architecture**: Securely manage multiple teams and projects from a single dashboard.
- **Interactive Commands**: Command CodeCrow directly from PR comments using `/codecrow ask`, `/codecrow analyze`, `/codecrow review`, `/codecrow summarize`, and `/codecrow qa-doc`.
- **QA Auto-Documentation**: Automatically generate QA testing documentation from PR analysis, store the latest document per PR in the CodeCrow dashboard, and post or update it on linked Jira tickets. Task IDs are auto-detected from branch names, PR titles, or PR descriptions — or you can specify one explicitly with `/codecrow qa-doc PROJ-123`.
- **Issue Lifecycle**: Automatic tracking of resolved vs. open issues across analyses with deterministic and AI-based reconciliation.
- **Bring Your Own Model**: Connect OpenRouter, OpenAI, Anthropic, Google AI, Google Vertex AI, or an OpenAI-compatible endpoint such as vLLM, Ollama, or Cloudflare Workers AI.

## Documentation

For full setup guides, architectural deep-dives, and API reference, please visit our documentation portal:

👉 [**codecrow.app/docs**](https://codecrow.app/docs/getting-started)

---

## Architecture at a glance

High level components:

- **Web frontend** (`frontend/`) – pinned React submodule for workspaces, projects, dashboards, RAG controls, and issue views.
- **Web server / API** (`java-ecosystem/services/web-server/`) – main backend API, auth, workspaces/projects, and orchestration.
- **Pipeline agent** (`java-ecosystem/services/pipeline-agent/`) – receives VCS webhooks, fetches repo/PR data, and coordinates analysis.
- **Analysis plugins** (`analysis-plugins/`) – neutral contracts and independently owned language, framework, and domain implementations.
- **Inference orchestrator** (`python-ecosystem/inference-orchestrator/`) – assembles bounded review stages, enforces evidence gates, and calls the configured review model. MCP tools are loaded only for flows that require them.
- **RAG pipeline** (`python-ecosystem/rag-pipeline/`) – builds semantic and deterministic repository context in **Qdrant**.
- **PostgreSQL, Redis, and Qdrant** – durable application state, queues/liveness coordination, and vector/architecture context respectively.

See the [system design](https://codecrow.app/docs/developer/architecture),
[plugin architecture](https://codecrow.app/docs/developer/plugin-architecture),
and [review quality controls](https://codecrow.app/docs/developer/review-quality)
for the detailed invariants and failure behavior.

## Self-Hosting and Build Verification

The interactive setup configures secrets and chooses OpenRouter or Ollama for
embeddings. The local production build fetches and checks out the latest commit
from the frontend submodule's configured `main` branch, rejects local frontend
drift, recreates the two isolated Python 3.11 CI environments, and runs the same
Python, plugin-boundary, Maven `verify`, and observable-image Buildx gates as
CI/CD. Only after every gate passes does it replace the local Compose services
with those validated images and wait for health checks.

```bash
cd deployment
./setup.sh
./build/production-build.sh
```

| Gate                | Command or CI Behavior                                                                                                            |
| :------------------ | :-------------------------------------------------------------------------------------------------------------------------------- |
| Java                | `cd java-ecosystem && mvn clean verify`                                                                                           |
| Python              | CI and `production-build.sh` install each service's requirements separately, then run plugin-contract, RAG unit/integration, inference unit/integration, and review-quality suites |
| Plugin Boundary     | `python3 tools/validate_plugin_boundaries.py` prevents concrete implementations from leaking into generic hosts                   |
| Docker Images       | CI pushes and the local build loads images from the same contexts and observable Dockerfiles                                      |
| Production Workflow | Manual dispatch; deployment waits for both Java/build and full Python test jobs unless explicitly deploying existing images       |

## Contributing

Contributions are welcome. Please see our [Development Guide](https://codecrow.app/docs/developer/dev-setup) for more information.

## License

This project is licensed under the [FSL-1.1-MIT (Functional Source License)](LICENSE). You can use, modify, and self-host it freely — the only restriction is that you may not use it to build a competing commercial code-review product. Every version automatically converts to a full MIT license two years after its release.

> **Note:** The hosted service (codecrow-cloud) is proprietary and not covered by this license.
