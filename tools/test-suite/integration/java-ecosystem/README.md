# Java Integration Tests

This directory contains scripts and utilities for running **integration tests** across the CodeCrow Java ecosystem.

Integration tests live under `src/it/java` in each module and are executed by **Maven Failsafe** plugin (not Surefire). They use **Testcontainers** to spin up isolated Docker containers for PostgreSQL and Redis — **your main database is never touched**.

## Database & Infrastructure

| Resource          | How it works                                                                           |
| ----------------- | -------------------------------------------------------------------------------------- |
| **PostgreSQL**    | Testcontainers spins up a fresh `postgres:16-alpine` container with DB `codecrow_test` |
| **Redis**         | Testcontainers spins up a fresh `redis:7-alpine` container                             |
| **Schema**        | `spring.jpa.hibernate.ddl-auto=create-drop` — created on boot, dropped on shutdown     |
| **Cleanup**       | `DatabaseCleaner` truncates all tables between tests for isolation                     |
| **External APIs** | WireMock stubs — no real GitHub/GitLab/Bitbucket calls                                 |

> **TL;DR:** A completely ephemeral, isolated environment. Zero risk to production or dev databases.

## Prerequisites

- **Docker** running (required by Testcontainers)
- **Java 17+**
- **Maven 3.8+**
- Project built at least once: `cd java-ecosystem && mvn install -DskipTests`

## Modules with Integration Tests

### Libraries (`libs/`)

| Module              | IT Files | Focus                                                      |
| ------------------- | -------- | ---------------------------------------------------------- |
| **core**            | 12       | Repository CRUD, queries, constraints                      |
| **analysis-engine** | 8        | Branch sweep, line clamping, deterministic tracking, locks |
| **vcs-client**      | 5        | GitHub/GitLab/Bitbucket API, error handling, token refresh |
| **commit-graph**    | 2        | Commit persistence, branch-commit mapping                  |
| **file-content**    | 2        | File content persistence, snapshot service                 |
| **email**           | 2        | SMTP delivery (GreenMail), template rendering              |
| **rag-engine**      | 2        | RAG pipeline client, index tracking                        |
| **security**        | 3        | JWT validation, token encryption, UserDetailsService       |
| **queue**           | 3        | Redis queue pub/sub, isolation, connection factory         |

### Services (`services/`)

| Module             | IT Files | Focus                                                           |
| ------------------ | -------- | --------------------------------------------------------------- |
| **web-server**     | 10       | REST API controllers (auth, projects, workspaces, issues, etc.) |
| **pipeline-agent** | 8        | E2E branch recompute, PR analysis, reconcile, webhooks          |

**Total: 57 IT files, ~370 @Test methods across 11 modules**

## Scripts

### Run All Integration Tests

```bash
./run-tests.sh
```

Runs all integration tests across all modules and generates a summary report in `test-run/`.

Options:

- No arguments: run all modules sequentially
- Module name: run a single module (e.g., `./run-tests.sh core`)
- `--parallel`: run with `-T 1C` (one thread per CPU core)
- `--failfast`: stop on first module failure

```bash
# Examples
./run-tests.sh                      # All modules
./run-tests.sh core                 # Only libs/core
./run-tests.sh web-server           # Only web-server
./run-tests.sh --failfast           # Stop on first failure
./run-tests.sh --parallel           # Parallel execution
```

### Check IT Coverage

```bash
./check-coverage.sh
```

Runs integration tests with JaCoCo coverage and displays coverage per module.

```bash
./check-coverage.sh                 # All modules
./check-coverage.sh analysis-engine # Single module
```

## Test Output

Test results are stored in `test-run/` directory (git-ignored):

- `test-run/it-summary.txt` — overall integration test summary
- `test-run/[module]-it-results-*.txt` — per-module Failsafe output
- `test-run/it-coverage-report.txt` — coverage summary

## Running Individual Module Tests

From the `java-ecosystem` directory:

```bash
# Run ITs for a specific module (skip unit tests)
mvn verify -pl libs/core -Dskip.surefire.tests=true

# Run a specific IT class
mvn verify -pl libs/analysis-engine -Dit.test=BranchSweepRecomputeIT -Dskip.surefire.tests=true

# Run ITs with coverage
mvn verify jacoco:report -pl services/web-server -Dskip.surefire.tests=true
```

## Coverage Targets

| Module          | Target Coverage |
| --------------- | --------------- |
| core            | 70%+            |
| analysis-engine | 80%+            |
| security        | 80%+            |
| vcs-client      | 70%+            |
| web-server      | 70%+            |
| pipeline-agent  | 70%+            |
| All others      | 60%+            |

## Test Conventions

### Naming

- IT classes: `[Concept]IT.java` (e.g., `BranchSweepRecomputeIT.java`)
- Test methods: `should[Expected]_when[Condition]` or descriptive names

### Structure

```
src/it/java/          → Integration test sources
src/it/resources/     → IT-specific config (application-it.properties, fixtures)
```

### Shared Infrastructure (test-support module)

- `SharedPostgresContainer` / `SharedRedisContainer` — singleton Testcontainers
- `PostgresContainerInitializer` / `RedisContainerInitializer` — Spring context initializers
- `@IntegrationTest` — meta-annotation (Spring profile "it" + container initializers)
- `DatabaseCleaner` — truncates all tables between tests
- Fixtures: `UserFixture`, `ProjectFixture`, `WorkspaceFixture`, `BranchIssueFixture`
- Assertions: `BranchIssueAssert` — AssertJ custom assertion for branch issues

## Docker Cleanup

Testcontainers are marked reusable (`withReuse(true)`) for faster local runs. To clean up:

```bash
# Remove all Testcontainers
docker rm -f $(docker ps -aq --filter label=org.testcontainers)
```
