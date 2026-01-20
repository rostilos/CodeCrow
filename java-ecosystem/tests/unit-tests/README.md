# Java Unit Tests

This directory contains scripts and utilities for running unit tests across the CodeCrow Java ecosystem.

## Overview

The CodeCrow Java ecosystem consists of multiple modules:

### Libraries (`libs/`)
- **core** - Core domain models, entities, and shared utilities
- **analysis-engine** - Code analysis and review engine
- **email** - Email notification services
- **rag-engine** - RAG (Retrieval-Augmented Generation) indexing and operations
- **security** - JWT authentication and security utilities
- **vcs-client** - VCS (Version Control System) API clients (Bitbucket, GitHub, GitLab)

### MCP Servers (`mcp-servers/`)
- **platform-mcp** - Platform MCP server
- **vcs-mcp** - VCS MCP server

### Services (`services/`)
- **pipeline-agent** - Pipeline agent for code review processing
- **web-server** - Web server API

## Testing Framework

All modules use:
- **JUnit 5** (Jupiter) - Testing framework
- **Mockito** - Mocking framework
- **AssertJ** - Fluent assertions
- **JaCoCo** - Code coverage reporting
- **MockWebServer** - HTTP mocking for API clients (where applicable)

## Scripts

### Run All Tests

```bash
./run-tests.sh
```

Runs all unit tests across all modules and generates a summary report in `test-run/` directory.

Options:
- No arguments: Run all tests
- Module name: Run tests for specific module (e.g., `./run-tests.sh core`)

### Check Coverage

```bash
./check-coverage.sh
```

Runs tests with JaCoCo coverage and displays coverage summary in CLI.

Options:
- No arguments: Check coverage for all modules
- Module name: Check coverage for specific module (e.g., `./check-coverage.sh vcs-client`)

## Test Output

Test results are stored in `test-run/` directory (git-ignored):
- `test-run/summary.txt` - Overall test summary
- `test-run/[module]-results.txt` - Per-module test results
- `test-run/coverage-report.txt` - Coverage summary

## Running Individual Module Tests

From the `java-ecosystem` directory:

```bash
# Run tests for a specific module
mvn test -pl libs/core
mvn test -pl libs/vcs-client
mvn test -pl libs/rag-engine

# Run with coverage
mvn test jacoco:report -pl libs/security

# Run specific test class
mvn test -pl libs/email -Dtest=EmailServiceImplTest
```

## Coverage Targets

| Module | Target Coverage |
|--------|----------------|
| core | 60%+ |
| security | 80%+ |
| email | 80%+ |
| vcs-client | 60%+ |
| rag-engine | 60%+ |
| analysis-engine | 60%+ |

## Writing Tests

### Naming Convention
- Test classes: `[ClassName]Test.java`
- Test methods: `test[MethodName]_[Scenario]` or `should[ExpectedBehavior]_when[Condition]`

### Test Location
Tests should be placed in `src/test/java` mirroring the main source structure.

### Example Test Structure

```java
@ExtendWith(MockitoExtension.class)
class MyServiceTest {

    @Mock
    private DependencyService dependencyService;

    @InjectMocks
    private MyService service;

    @Test
    void testMethodName_Success() {
        // Given
        when(dependencyService.getData()).thenReturn("data");

        // When
        String result = service.process();

        // Then
        assertThat(result).isEqualTo("expected");
        verify(dependencyService).getData();
    }
}
```

## Troubleshooting

### Common Issues

1. **Module access errors** - Java module system restrictions when using reflection
   - Solution: Use `@Mock` annotations instead of concrete instances for core model classes

2. **Missing dependencies** - Test dependencies not available
   - Solution: Ensure `spring-boot-starter-test` is in pom.xml with `<scope>test</scope>`

3. **Mockito strict stubbing** - Unused stubs cause test failures
   - Solution: Use `lenient().when(...)` for setUp stubs not used in all tests
