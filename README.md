# CodeCrow

Automated code review system for Bitbucket Cloud using AI and Model Context Protocol (MCP).

## Quick Links

📚 **[Complete Documentation](docs/README.md)**

- [Getting Started](docs/02-getting-started.md) - Installation and setup guide
- [Architecture Overview](docs/03-architecture.md) - System design and components
- [Configuration Reference](docs/05-configuration.md) - Configuration options
- [API Documentation](docs/06-api-reference.md) - REST API reference
- [Deployment Guide](docs/09-deployment.md) - Production deployment
- [Development Guide](docs/10-development.md) - Contributing and development
- [Troubleshooting](docs/11-troubleshooting.md) - Common issues and solutions

## Overview

CodeCrow analyzes code changes in pull requests and branches using:
- AI-powered analysis via OpenRouter
- RAG (Retrieval-Augmented Generation) for code context
- MCP servers for secure repository access
- Automated issue tracking and resolution detection

## Quick Start

1. **Configure services**:
```bash
cp deployment/docker-compose-sample.yml deployment/docker-compose.yml
cp deployment/config/java-shared/application.properties.sample deployment/config/java-shared/application.properties
cp deployment/config/mcp-client/.env.sample deployment/config/mcp-client/.env
cp deployment/config/rag-pipeline/.env.sample deployment/config/rag-pipeline/.env
cp deployment/config/web-frontend/.env.sample deployment/config/web-frontend/.env
```

2. **Update credentials** in config files:
   - JWT secret and encryption key in `application.properties`
   - OpenRouter API key in `rag-pipeline/.env`

3. **Build and start**:
```bash
./tools/production-build.sh
```

4. **Access application**:
   - Frontend: http://localhost:8080
   - API: http://localhost:8081
   - Swagger: http://localhost:8081/swagger-ui-custom.html

See [Getting Started Guide](docs/02-getting-started.md) for detailed instructions.

## Table of Contents

- Project overview
- Prerequisites
- Configuration
  - application.properties sample
  - important keys to update
- Run with Docker Compose (recommended)
  - Quick start
  - Common commands
- CI Integration — upload repo archive from CI (new)
- Build from source (Maven)
  - Build artifacts
  - Run modules individually
- Troubleshooting & notes
- Development tips
- Contributing

---

## Project overview

codecrow is split into multiple modules:
- codecrow-web-application / codecrow-web-server — the primary web application and backend services
- codecrow-pipeline-agent — processing/pipeline worker
- codecrow-mcp-client — MCP client used for review/LLM integration
- codecrow-mcp-servers— MCP toolkit.
- codecrow-rag-pipeline — RAG (Retrieval-Augmented Generation) pipeline for semantic code search (Python)
- codecrow-web-frontend — frontend for users (served on its own port)
- codecrow-core, codecrow-security, codecrow-vcs-client — shared libraries and clients

A sample docker-compose file (`docker-compose-sample.yml`) is included showing how the components work together for local development.

---

## Prerequisites

- Docker & Docker Compose (compose v1 or v2) installed and working
- Java 17+ and Maven (if you want to build/run modules locally without Docker)
- (Optional) git for fetching submodules or switching branches
- Recommended: at least 4GB free RAM available for Docker containers

---

## Configuration

Application properties are provided in `config/application.properties.sample`. When running with Docker Compose the sample file is mounted into containers at `/app/config/application.properties` (see `docker-compose-sample.yml`).

Important keys in `config/application.properties.sample`:

- codecrow.security.jwtSecret — JWT secret used by services (please change in production)
- codecrow.security.jwtExpirationMs — token TTL (ms)
- codecrow.security.projectJwtExpirationMs — project token TTL (ms)
- codecrow.security.encryption-key — symmetric key for any encryption functions (change before production)
- codecrow.web.base.url — base URL used by the app (e.g., `http://localhost:8080`)
- codecrow.mcp.client.url — MCP client endpoint (default: `http://host.docker.internal:8000/review`)
- springdoc.* — swagger/ui paths configuration

Guidance:
- Copy `config/application.properties.sample` to `config/application.properties` and update any secrets and URLs before running.
- When running inside Docker Compose the file is mounted and read automatically by services (see `SPRING_CONFIG_LOCATION` env in compose).
- Keep secrets out of version control; use environment variables or a secrets manager for production.

---

## Run with Docker Compose (recommended for development)

A ready-to-use sample compose file is provided as `docker-compose-sample.yml`. You can use it as-is for local development.

Quick start (from repository root):

1. Copy sample to active compose filename (optional):
   - cp docker-compose-sample.yml docker-compose.yml

2. Start services:
   - docker-compose -f docker-compose-sample.yml up --build -d

This command:
- Builds service images (web application, pipeline-agent, mcp-client, frontend) where Dockerfiles are present.
- Spins up PostgreSQL (codecrow_postgres), Redis, and all application containers.
- Mounts `config/application.properties` into the application containers so the configuration in `./config` is used.

Important exposed ports (defaults in sample):
- Frontend: http://localhost:8080
- Web application API: http://localhost:8081
- Pipeline agent API: http://localhost:8082
- MCP client port: 8000 ( Please note that it must be inaccessible on the public network; with this in mind, the relevant protection mechanisms have not been implemented. )
- RAG Pipeline API: http://localhost:8001
- Postgres: 5432 (local binding)
- Redis: 6379 (local binding)
- MongoDB: 27017 (local binding, for RAG)

Common compose commands:
- Start (build if necessary): docker-compose -f docker-compose-sample.yml up --build -d
- Stop: docker-compose -f docker-compose-sample.yml down
- View logs: docker-compose -f docker-compose-sample.yml logs -f web-server
- Recreate a single service: docker-compose -f docker-compose-sample.yml up --build -d web-server
- Remove volumes (if you need a clean DB): docker-compose -f docker-compose-sample.yml down -v

Notes:
- The sample sets DB credentials and database name (POSTGRES_DB: codecrow_ai, POSTGRES_USER: codecrow_user, POSTGRES_PASSWORD: codecrow_pass). Change these for production.
- The compose sample maps `./config/application.properties` into containers. Ensure you have `config/application.properties` present (copy from sample and edit).
- The MCP client uses `host.docker.internal` to reach the host machine. On Linux this might require `host-gateway` support (compose file already sets extra_hosts for pipeline-agent). If it fails, replace `host.docker.internal` with an accessible host address.

---

## CI Integration — upload repo archive from CI (new)

When running in CI (e.g. Bitbucket Pipelines) you can send a repository archive to the pipeline-agent which will forward it to the analysis-worker (MCP) and stream incremental results back to the pipeline logs.

Implementation in this repo:
- `docs/pipeline-integration.md` — full examples for curl and Bitbucket Pipelines.
- `bitbucket-pipeline-uploader/` — a small Dockerfile and `upload_repo.sh` script you can use as the image that runs inside the pipeline and uploads the archive automatically.
- `Makefile` — local helper to create a sample payload, make an archive and upload it to a running pipeline-agent for local testing.

Quick pointers:
- Endpoint: POST `/api/processing/bitbucket/webhook-multipart` (multipart form: payload JSON + file archive)
- The pipeline-agent streams NDJSON events (progress, issues, final) back to the caller; the uploader script and Makefile helper read and print those events.
- See `docs/pipeline-integration.md` for exact curl commands and pipeline snippet.

---

## RAG Pipeline — Semantic Code Search (new)

The RAG (Retrieval-Augmented Generation) pipeline provides semantic search and context retrieval for code repositories.

### Features
- **Semantic Search**: Find code using natural language queries
- **PR Context**: Automatically retrieve relevant code context for pull request reviews
- **Code-Aware Chunking**: Intelligent splitting that preserves code structure
- **Incremental Updates**: Only re-index changed files
- **Namespace Isolation**: Separate indices per workspace/project/branch

### Quick Start

1. Set your OpenAI API key:
   ```bash
   echo "OPENAI_API_KEY=sk-your-key-here" >> .env
   ```

2. Start RAG Pipeline services:
   ```bash
   ./start-rag-pipeline.sh
   ```
   
   Or manually:
   ```bash
   docker-compose up -d mongodb rag-pipeline
   ```

3. Verify it's running:
   ```bash
   curl http://localhost:8001/health
   ```

### API Endpoints
- Health: `GET http://localhost:8001/health`
- Index repository: `POST /index/repository`
- Update files: `POST /index/update-files`
- Semantic search: `POST /query/search`
- Get PR context: `POST /query/pr-context`
- API docs: `http://localhost:8001/docs`

### Documentation
Full documentation available in `codecrow-rag-pipeline/`:
- `README.md` — Module overview and usage
- `INTEGRATION_GUIDE.md` — How to integrate with pipeline-agent
- `DEPLOYMENT.md` — Production deployment guide
- `IMPLEMENTATION_SUMMARY.md` — Technical details

### Requirements
- MongoDB (included in docker-compose)
- OpenRouter API key for embeddings (get from https://openrouter.ai/)
- Python 3.11+ (if running outside Docker)

---

## Build from source (Maven)

If you prefer to run services without Docker, you can build them using Maven and run Spring Boot modules directly.

1. Build the whole multi-module project:
   - mvn -T1C -DskipTests package

2. Run a service (example: web application) with a custom config location:
   - mvn -pl codecrow-web-application spring-boot:run -Dspring-boot.run.jvmArguments="-Dspring.config.location=file:config/application.properties" -Dspring-boot.run.arguments="--server.port=8081"

3. Run pipeline-agent similarly:
   - mvn -pl codecrow-pipeline-agent spring-boot:run -Dspring-boot.run.jvmArguments="-Dspring.config.location=file:config/application.properties" -Dspring-boot.run.arguments="--server.port=8082"

Notes:
- The `-pl` option runs a specific module and `-am` can be added to build required modules as needed.
- Ensure the database and redis are available (you can start just Postgres and Redis with docker-compose and run apps locally).

Run tests:
- mvn test

## Production build script

A convenience script is provided at `scripts/production-build.sh` for building artifacts, synchronizing the frontend repository, and deploying via Docker Compose. The script performs the following steps:

1. Ensures frontend code is cloned or updated from the configured `FRONTEND_REPO_URL` (it will clone if the directory does not exist, or fetch & reset to origin/main if it does).
2. Runs `mvn clean package -DskipTests` to build Java artifacts.
3. Runs `docker compose down --remove-orphans` to stop existing services.
4. Runs `docker compose up -d --build --wait` to build images and start services, then prints `docker compose ps`.

Usage:
- Make the script executable and run it from the repository root:
  - chmod +x scripts/production-build.sh
  - ./scripts/production-build.sh

Notes:
- The script expects `git` and SSH access to the frontend repo defined in the script (`FRONTEND_REPO_URL`). Update the variable in the script if you need to point to a different frontend repository or branch.
- It uses `docker compose` (the Docker CLI plugin). If your environment only has the older `docker-compose` binary, update the script or ensure compatibility.
- Use caution when running the script in production; review and update environment variables, secrets, and compose files before running.
- The script will run `mvn clean package -DskipTests` which rebuilds all modules. Ensure you have Java and Maven installed on the host where you run the script.

---

## Database migrations

Flyway DB migrations are available under:
- codecrow-core/src/main/resources/db/migration/

Migrations are executed automatically by Spring Boot / Flyway on startup if Flyway is enabled in configuration. Ensure DB connectivity configuration matches the docker compose or your local DB instance.

---

## Logs and persistence

- Persistent data for Postgres and Redis are configured as Docker volumes in the compose file (postgres_data, redis_data).
- Application logs are written to container volumes (see docker-compose sample volume mappings such as `web_logs:/app/logs` and `web_frontend_logs:/app/logs`).

---

## Troubleshooting

- Database connection errors:
  - Make sure Postgres is started and healthy in Docker Compose.
  - Check credentials in `config/application.properties` and `docker-compose-sample.yml`.
  - Check container logs: docker-compose -f docker-compose-sample.yml logs -f postgres

- MCP client connectivity:
  - The sample uses `http://host.docker.internal:8000/review`. On Linux this might require `host-gateway` support (compose file already sets extra_hosts for pipeline-agent). If problems occur, change the value to a reachable address.

- Ports already in use:
  - Change mapped ports in `docker-compose-sample.yml` or stop services using those ports.

- Slow startup:
  - Healthchecks with start_period may delay dependent services. Inspect healthcheck configuration in `docker-compose-sample.yml`.

---

## Development tips

- Copy `config/application.properties.sample` -> `config/application.properties` and adjust secrets/URLs.
- Use `docker-compose -f docker-compose-sample.yml up postgres redis` to run only datastore services, then run modules locally via Maven for faster iterative development.
- To rebuild just one service image: docker-compose -f docker-compose-sample.yml build web-server

---

## Security & production notes

- Do not use the sample JWT secrets or encryption keys in production. Generate strong secrets or use a secrets manager.
- For production deployments, consider:
  - Using a proper secrets store (Vault, AWS Secrets Manager, etc.)
  - Running services under orchestration (Kubernetes) with appropriate resource limits
  - Enabling TLS and proper reverse proxy (nginx is provided as a sample config in `nginx/`)

---

## Contributing

- Follow existing code structure and module patterns.
- Run `mvn test` before creating pull requests.
- If modifying configuration, update `config/application.properties.sample` accordingly and document the change in this README.

---

## Useful commands summary

- Start everything:
  - docker-compose -f docker-compose-sample.yml up --build -d
- Stop everything:
  - docker-compose -f docker-compose-sample.yml down
- Build project:
  - mvn -T1C -DskipTests package
- Run a single module:
  - mvn -pl codecrow-web-application spring-boot:run -Dspring-boot.run.jvmArguments="-Dspring.config.location=file:config/application.properties" -Dspring-boot.run.arguments="--server.port=8081"
- Run tests:
  - mvn test

---

If you need the README updated with additional project-specific examples (e.g., step-by-step debugging instructions, environment variable examples, or CI/CD configuration), provide details and I will add them.
