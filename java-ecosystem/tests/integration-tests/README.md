# CodeCrow Integration Tests

Comprehensive integration and end-to-end test suite for the CodeCrow platform.

> **Note:** This is a **standalone module** that is NOT part of the main build. It must be built and run separately to avoid affecting production deployments.

## Overview

This module contains integration tests that validate the full functionality of the CodeCrow platform including:

- **VCS Connections**: CRUD operations for Bitbucket Cloud and GitLab connections
- **AI Connections**: CRUD operations for AI provider connections (OpenRouter, OpenAI, etc.)
- **User Authentication**: Registration, login, role management, 2FA, password reset
- **Workspace Management**: Creation, membership, settings, permissions
- **Project Management**: CRUD, VCS binding, AI binding, tokens
- **Analysis**: Webhook processing, PR analysis, incremental analysis, issue tracking
- **RAG Pipeline**: Indexing, incremental updates, search, relevance tracking
- **Security**: Role-based access control, input validation, data isolation

## Architecture

### Test Infrastructure

```
src/test/java/org/rostilos/codecrow/integration/
├── base/
│   └── BaseIntegrationTest.java    # Base class with common setup
├── builder/
│   ├── AIConnectionBuilder.java    # Fluent AI connection builder
│   ├── VcsConnectionBuilder.java   # Fluent VCS connection builder
│   └── ProjectBuilder.java         # Fluent project builder
├── config/
│   ├── TestContainersConfig.java   # PostgreSQL testcontainer
│   ├── WireMockConfig.java         # WireMock server configuration
│   └── TestCredentialsConfig.java  # External credentials binding
├── mock/
│   ├── BitbucketCloudMockSetup.java # Bitbucket API mocks
│   ├── GitLabMockSetup.java         # GitLab API mocks
│   ├── AIProviderMockSetup.java     # AI provider mocks
│   └── RagPipelineMockSetup.java    # RAG pipeline mocks
├── util/
│   ├── AuthTestHelper.java         # User/auth utilities
│   └── TestDataCleaner.java        # Database cleanup
└── tests/
    ├── ai/                          # AI connection tests
    ├── analysis/                    # Analysis and webhook tests
    ├── auth/                        # Authentication tests
    ├── project/                     # Project CRUD tests
    ├── rag/                         # RAG pipeline tests
    ├── security/                    # Security and permission tests
    ├── vcs/                         # VCS connection tests
    └── workspace/                   # Workspace management tests
```

### Technology Stack

- **JUnit Jupiter 5.10.2**: Test framework
- **Testcontainers 1.19.7**: PostgreSQL container for database isolation
- **WireMock 3.4.2**: HTTP mocking for external services
- **RestAssured 5.4.0**: REST API testing
- **Awaitility 4.2.0**: Asynchronous testing support

## Running Tests

### Prerequisites

1. Docker running (for Testcontainers)
2. Java 17+
3. Maven 3.8+
4. **CodeCrow modules installed in local Maven repository**

### Install Dependencies First

Since this module is standalone, you must first install the CodeCrow modules:

```bash
cd java-ecosystem
mvn clean install -DskipTests
```

### Run All Integration Tests

```bash
cd java-ecosystem/tests/integration-tests
mvn verify -DskipUnitTests
```

Or from the java-ecosystem root:

```bash
cd java-ecosystem
mvn verify -f tests/integration-tests/pom.xml -DskipUnitTests
```

### Run Specific Test Categories

```bash
# From integration-tests directory:
cd java-ecosystem/tests/integration-tests

# VCS Connection tests only
mvn verify -Dgroups=vcs

# Security tests only
mvn verify -Dgroups=security

# Analysis tests only
mvn verify -Dgroups=analysis

# RAG tests only
mvn verify -Dgroups=rag
```

### Run Single Test Class

```bash
cd java-ecosystem/tests/integration-tests
mvn verify -Dit.test=VcsConnectionCrudIT
```

### Run with Real External Services

Create `src/test/resources/test-credentials.yml` from the sample file and add real credentials:

```bash
cp src/test/resources/test-credentials.yml.sample src/test/resources/test-credentials.yml
# Edit with real credentials
```

Then run with the credentials profile:

```bash
mvn verify -pl tests/integration-tests -Dspring.profiles.active=test,credentials
```

## Configuration

### application-test.yml

Main test configuration including:
- Database settings (Testcontainers auto-configured)
- WireMock server URLs
- JWT and security settings
- Logging configuration

### test-credentials.yml

Optional file for testing with real external services (not committed):

```yaml
test:
  credentials:
    bitbucket:
      username: your-username
      app-password: your-app-password
      workspace-id: your-workspace
    gitlab:
      access-token: your-gitlab-token
      group-id: your-group-id
    openrouter:
      api-key: your-openrouter-key
```

## Writing New Tests

### Creating a New Test Class

1. Extend `BaseIntegrationTest`:

```java
@Tag("your-category")
class YourFeatureIT extends BaseIntegrationTest {
    
    @BeforeEach
    void setUpTest() {
        authTestHelper.initializeTestUsers();
    }
    
    @Test
    void shouldPerformSomeAction() {
        given()
            .spec(authenticatedAsAdmin())
            .body(requestBody)
        .when()
            .post("/api/{workspaceSlug}/your-endpoint", workspace.getSlug())
        .then()
            .statusCode(200);
    }
}
```

### Using WireMock for External Services

```java
@BeforeEach
void setUpTest() {
    BitbucketCloudMockSetup mock = new BitbucketCloudMockSetup(bitbucketCloudMock);
    mock.setupValidUserResponse();
    mock.setupRepositoriesResponse("workspace");
}
```

### Test Data Builders

```java
VcsConnection connection = new VcsConnectionBuilder()
    .withName("Test Connection")
    .withType(VcsType.BITBUCKET_CLOUD)
    .withWorkspace(testWorkspace)
    .build();
```

## Test Categories (Tags)

| Tag | Description |
|-----|-------------|
| `vcs` | VCS connection tests |
| `ai` | AI connection tests |
| `auth` | Authentication tests |
| `workspace` | Workspace management tests |
| `project` | Project CRUD tests |
| `analysis` | Analysis/webhook tests |
| `webhook` | Webhook-specific tests |
| `rag` | RAG pipeline tests |
| `security` | Security and permission tests |
| `fast` | Quick tests (<5s each) |
| `slow` | Long-running tests (>30s) |

## CI/CD Integration

### GitHub Actions Example

```yaml
- name: Install CodeCrow Modules
  run: |
    cd java-ecosystem
    mvn clean install -DskipTests

- name: Run Integration Tests
  run: |
    cd java-ecosystem/tests/integration-tests
    mvn verify \
      -DskipUnitTests \
      -Dspring.profiles.active=test
```

### Docker Compose for CI

A `docker-compose-test.yml` is available for running tests in CI environments without local Docker:

```bash
docker-compose -f docker-compose-test.yml up -d
mvn verify -pl tests/integration-tests -DuseExternalContainers=true
```

## Troubleshooting

### Container Startup Issues

If Testcontainers fail to start:
1. Ensure Docker daemon is running
2. Check Docker memory limits (≥4GB recommended)
3. Clear Docker cache: `docker system prune`

### WireMock Port Conflicts

Default ports:
- 8081: VCS providers (Bitbucket/GitLab)
- 8082: AI providers
- 8083: RAG pipeline

Override in `application-test.yml` if needed.

### Database Connection Issues

Testcontainers uses a dynamic port. If you see connection errors:
1. Check Docker is running
2. Verify network access
3. Check `@ServiceConnection` annotation is present

## Contributing

1. Follow the existing test structure
2. Add appropriate `@Tag` annotations
3. Use `@DisplayName` for readable test names
4. Clean up test data in `@AfterEach`
5. Mock external services with WireMock
