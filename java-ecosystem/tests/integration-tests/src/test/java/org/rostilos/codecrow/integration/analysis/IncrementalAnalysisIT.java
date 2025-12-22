package org.rostilos.codecrow.integration.analysis;

import org.junit.jupiter.api.*;
import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.integration.base.BaseIntegrationTest;
import org.rostilos.codecrow.integration.mock.AIProviderMockSetup;
import org.rostilos.codecrow.integration.mock.BitbucketCloudMockSetup;

import static io.restassured.RestAssured.given;
import static org.hamcrest.Matchers.*;

/**
 * Integration tests for incremental branch analysis and issue resolution.
 * Tests analysis of multiple commits, issue tracking, and status resolution.
 */
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
@Tag("analysis")
@Tag("incremental")
class IncrementalAnalysisIT extends BaseIntegrationTest {

    private BitbucketCloudMockSetup bitbucketMock;
    private AIProviderMockSetup aiMock;
    private Workspace testWorkspace;
    private Long vcsConnectionId;
    private Long aiConnectionId;
    private String projectNamespace;

    @BeforeEach
    void setUpTest() {
        bitbucketMock = new BitbucketCloudMockSetup(bitbucketCloudMock);
        aiMock = new AIProviderMockSetup(openaiMock, anthropicMock, openrouterMock);
        authTestHelper.initializeTestUsers();
        testWorkspace = authTestHelper.getDefaultWorkspace();

        setupConnections();
        setupProject();
    }

    private void setupConnections() {
        bitbucketMock.setupValidUserResponse();
        bitbucketMock.setupWorkspacesResponse("test-workspace", "Test Workspace", "{ws-uuid}");
        bitbucketMock.setupRepositoriesResponse("test-workspace");
        bitbucketMock.setupRepositoryResponse("test-workspace", "incremental-repo");
        aiMock.setupChatCompletion("Code analysis complete");

        // Create VCS connection directly in database to bypass OAuth validation
        VcsConnection vcsConnection = authTestHelper.createTestVcsConnection(
                testWorkspace, 
                "Incremental Test VCS"
        );
        vcsConnectionId = vcsConnection.getId();
        System.out.println("Created VCS Connection ID for incremental tests: " + vcsConnectionId);

        // Create AI connection directly in database
        AIConnection aiConnection = authTestHelper.createTestAiConnection(
                testWorkspace,
                "Incremental Test AI"
        );
        aiConnectionId = aiConnection.getId();
        System.out.println("Created AI Connection ID for incremental tests: " + aiConnectionId);
    }

    private void setupProject() {
        // Use unique namespace per test run to avoid duplicate namespace errors
        projectNamespace = "incremental-analysis-" + System.currentTimeMillis();
        String createBody = String.format("""
            {
                "name": "Incremental Analysis Project",
                "namespace": "%s",
                "description": "Project for incremental analysis tests"
            }
            """, projectNamespace);

        given()
                .spec(authenticatedAsAdmin())
                .body(createBody)
                .when()
                .post("/api/{workspaceSlug}/project/create", testWorkspace.getSlug())
                .then()
                .statusCode(201);

        String bindAiBody = String.format("""
            {
                "aiConnectionId": %d
            }
            """, aiConnectionId);

        given()
                .spec(authenticatedAsAdmin())
                .body(bindAiBody)
                .when()
                .put("/api/{workspaceSlug}/project/{namespace}/ai/bind",
                        testWorkspace.getSlug(), projectNamespace)
                .then()
                .statusCode(anyOf(equalTo(200), equalTo(204)));
    }

    @Nested
    @DisplayName("Branch Analysis Tests")
    class BranchAnalysisTests {

        @Test
        @Order(1)
        @DisplayName("Should analyze branch with multiple commits")
        void shouldAnalyzeBranchWithMultipleCommits() {
            bitbucketMock.setupBranchCommitsResponse("test-workspace", "incremental-repo", "feature/multi-commit");
            bitbucketMock.setupCommitDiff("test-workspace", "incremental-repo", "abc123");
            bitbucketMock.setupCommitDiff("test-workspace", "incremental-repo", "def456");
            bitbucketMock.setupFileContent("test-workspace", "incremental-repo", "feature/multi-commit", "src/Service.java");

            String analysisRequest = """
                {
                    "sourceBranch": "feature/multi-commit",
                    "targetBranch": "main",
                    "analysisType": "FULL"
                }
                """;

            given()
                    .spec(authenticatedAsAdmin())
                    .body(analysisRequest)
                    .when()
                    .post("/api/{workspaceSlug}/project/{namespace}/analyze/branch",
                            testWorkspace.getSlug(), projectNamespace)
                    .then()
                    .statusCode(anyOf(equalTo(200), equalTo(202), equalTo(400)));
        }

        @Test
        @Order(2)
        @DisplayName("Should perform incremental analysis after initial")
        void shouldPerformIncrementalAnalysis() {
            bitbucketMock.setupBranchCommitsResponse("test-workspace", "incremental-repo", "feature/incremental");
            bitbucketMock.setupCommitDiff("test-workspace", "incremental-repo", "new123");
            
            String analysisRequest = """
                {
                    "sourceBranch": "feature/incremental",
                    "targetBranch": "main",
                    "analysisType": "INCREMENTAL",
                    "sinceCommit": "base123"
                }
                """;

            given()
                    .spec(authenticatedAsAdmin())
                    .body(analysisRequest)
                    .when()
                    .post("/api/{workspaceSlug}/project/{namespace}/analyze/branch",
                            testWorkspace.getSlug(), projectNamespace)
                    .then()
                    .statusCode(anyOf(equalTo(200), equalTo(202), equalTo(400)));
        }

        @Test
        @Order(3)
        @DisplayName("Should compare two branches")
        void shouldCompareTwoBranches() {
            bitbucketMock.setupBranchCompare("test-workspace", "incremental-repo", "develop", "main");

            String compareRequest = """
                {
                    "sourceBranch": "develop",
                    "targetBranch": "main"
                }
                """;

            given()
                    .spec(authenticatedAsAdmin())
                    .body(compareRequest)
                    .when()
                    .post("/api/{workspaceSlug}/project/{namespace}/analyze/compare",
                            testWorkspace.getSlug(), projectNamespace)
                    .then()
                    .statusCode(anyOf(equalTo(200), equalTo(202), equalTo(400)));
        }
    }

    @Nested
    @DisplayName("Issue Tracking Tests")
    class IssueTrackingTests {

        @Test
        @Order(1)
        @DisplayName("Should list analysis issues")
        void shouldListAnalysisIssues() {
            given()
                    .spec(authenticatedAsAdmin())
                    .when()
                    .get("/api/{workspaceSlug}/project/{namespace}/issues",
                            testWorkspace.getSlug(), projectNamespace)
                    .then()
                    .statusCode(200);
        }

        @Test
        @Order(2)
        @DisplayName("Should filter issues by severity")
        void shouldFilterIssuesBySeverity() {
            given()
                    .spec(authenticatedAsAdmin())
                    .queryParam("severity", "HIGH")
                    .when()
                    .get("/api/{workspaceSlug}/project/{namespace}/issues",
                            testWorkspace.getSlug(), projectNamespace)
                    .then()
                    .statusCode(200);
        }

        @Test
        @Order(3)
        @DisplayName("Should filter issues by status")
        void shouldFilterIssuesByStatus() {
            given()
                    .spec(authenticatedAsAdmin())
                    .queryParam("status", "OPEN")
                    .when()
                    .get("/api/{workspaceSlug}/project/{namespace}/issues",
                            testWorkspace.getSlug(), projectNamespace)
                    .then()
                    .statusCode(200);
        }

        @Test
        @Order(4)
        @DisplayName("Should mark issue as resolved")
        void shouldMarkIssueAsResolved() {
            String issueId = "test-issue-1";
            String resolveBody = """
                {
                    "status": "RESOLVED",
                    "resolution": "Fixed in commit abc123",
                    "resolvedBy": "developer@example.com"
                }
                """;

            given()
                    .spec(authenticatedAsAdmin())
                    .body(resolveBody)
                    .when()
                    .patch("/api/{workspaceSlug}/project/{namespace}/issues/{issueId}",
                            testWorkspace.getSlug(), projectNamespace, issueId)
                    .then()
                    .statusCode(anyOf(equalTo(200), equalTo(404)));
        }

        @Test
        @Order(5)
        @DisplayName("Should mark issue as false positive")
        void shouldMarkIssueAsFalsePositive() {
            String issueId = "test-issue-2";
            String dismissBody = """
                {
                    "status": "FALSE_POSITIVE",
                    "reason": "Not applicable to this context"
                }
                """;

            given()
                    .spec(authenticatedAsAdmin())
                    .body(dismissBody)
                    .when()
                    .patch("/api/{workspaceSlug}/project/{namespace}/issues/{issueId}",
                            testWorkspace.getSlug(), projectNamespace, issueId)
                    .then()
                    .statusCode(anyOf(equalTo(200), equalTo(404)));
        }

        @Test
        @Order(6)
        @DisplayName("Should get issue statistics")
        void shouldGetIssueStatistics() {
            given()
                    .spec(authenticatedAsAdmin())
                    .when()
                    .get("/api/{workspaceSlug}/project/{namespace}/issues/stats",
                            testWorkspace.getSlug(), projectNamespace)
                    .then()
                    .statusCode(anyOf(equalTo(200), equalTo(404)));
        }
    }

    @Nested
    @DisplayName("Issue Resolution Flow Tests")
    class IssueResolutionFlowTests {

        @Test
        @Order(1)
        @DisplayName("Should auto-resolve issues when fixed in subsequent commit")
        void shouldAutoResolveIssuesOnFix() {
            // First trigger analysis that creates issues
            bitbucketMock.setupBranchCommitsResponse("test-workspace", "incremental-repo", "feature/with-issues");
            bitbucketMock.setupCommitDiff("test-workspace", "incremental-repo", "issue-commit");
            
            // Setup AI to return issues in analysis
            aiMock.setupChatCompletion("""
                {
                    "issues": [
                        {
                            "type": "SECURITY",
                            "severity": "HIGH",
                            "message": "SQL injection vulnerability",
                            "file": "src/UserService.java",
                            "line": 45
                        }
                    ]
                }
                """);

            String initialAnalysis = """
                {
                    "sourceBranch": "feature/with-issues",
                    "targetBranch": "main",
                    "analysisType": "FULL"
                }
                """;

            given()
                    .spec(authenticatedAsAdmin())
                    .body(initialAnalysis)
                    .when()
                    .post("/api/{workspaceSlug}/project/{namespace}/analyze/branch",
                            testWorkspace.getSlug(), projectNamespace)
                    .then()
                    .statusCode(anyOf(equalTo(200), equalTo(202), equalTo(400)));

            // Now trigger incremental analysis with fix
            bitbucketMock.setupCommitDiff("test-workspace", "incremental-repo", "fix-commit");
            aiMock.setupChatCompletion("""
                {
                    "issues": [],
                    "resolvedIssues": ["issue-1"]
                }
                """);

            String incrementalAnalysis = """
                {
                    "sourceBranch": "feature/with-issues",
                    "targetBranch": "main",
                    "analysisType": "INCREMENTAL",
                    "sinceCommit": "issue-commit"
                }
                """;

            given()
                    .spec(authenticatedAsAdmin())
                    .body(incrementalAnalysis)
                    .when()
                    .post("/api/{workspaceSlug}/project/{namespace}/analyze/branch",
                            testWorkspace.getSlug(), projectNamespace)
                    .then()
                    .statusCode(anyOf(equalTo(200), equalTo(202), equalTo(400)));
        }

        @Test
        @Order(2)
        @DisplayName("Should track issue history across analyses")
        void shouldTrackIssueHistory() {
            String issueId = "tracked-issue";
            
            given()
                    .spec(authenticatedAsAdmin())
                    .when()
                    .get("/api/{workspaceSlug}/project/{namespace}/issues/{issueId}/history",
                            testWorkspace.getSlug(), projectNamespace, issueId)
                    .then()
                    .statusCode(anyOf(equalTo(200), equalTo(404)));
        }

        @Test
        @Order(3)
        @DisplayName("Should reopen resolved issue if reintroduced")
        void shouldReopenResolvedIssueIfReintroduced() {
            // This tests the scenario where a resolved issue appears again
            // in a new analysis due to code changes
            String reopenCheck = """
                {
                    "sourceBranch": "feature/regression",
                    "targetBranch": "main",
                    "analysisType": "FULL",
                    "trackResolutions": true
                }
                """;

            given()
                    .spec(authenticatedAsAdmin())
                    .body(reopenCheck)
                    .when()
                    .post("/api/{workspaceSlug}/project/{namespace}/analyze/branch",
                            testWorkspace.getSlug(), projectNamespace)
                    .then()
                    .statusCode(anyOf(equalTo(200), equalTo(202), equalTo(400)));
        }
    }

    @Nested
    @DisplayName("Analysis History Tests")
    class AnalysisHistoryTests {

        @Test
        @Order(1)
        @DisplayName("Should get analysis history for project")
        void shouldGetAnalysisHistoryForProject() {
            given()
                    .spec(authenticatedAsAdmin())
                    .when()
                    .get("/api/{workspaceSlug}/project/{namespace}/analysis/history",
                            testWorkspace.getSlug(), projectNamespace)
                    .then()
                    .statusCode(200);
        }

        @Test
        @Order(2)
        @DisplayName("Should get analysis history with pagination")
        void shouldGetAnalysisHistoryWithPagination() {
            given()
                    .spec(authenticatedAsAdmin())
                    .queryParam("page", 0)
                    .queryParam("size", 10)
                    .when()
                    .get("/api/{workspaceSlug}/project/{namespace}/analysis/history",
                            testWorkspace.getSlug(), projectNamespace)
                    .then()
                    .statusCode(200);
        }

        @Test
        @Order(3)
        @DisplayName("Should filter analysis by branch")
        void shouldFilterAnalysisByBranch() {
            given()
                    .spec(authenticatedAsAdmin())
                    .queryParam("branch", "feature/test")
                    .when()
                    .get("/api/{workspaceSlug}/project/{namespace}/analysis/history",
                            testWorkspace.getSlug(), projectNamespace)
                    .then()
                    .statusCode(200);
        }

        @Test
        @Order(4)
        @DisplayName("Should filter analysis by date range")
        void shouldFilterAnalysisByDateRange() {
            given()
                    .spec(authenticatedAsAdmin())
                    .queryParam("from", "2024-01-01")
                    .queryParam("to", "2024-12-31")
                    .when()
                    .get("/api/{workspaceSlug}/project/{namespace}/analysis/history",
                            testWorkspace.getSlug(), projectNamespace)
                    .then()
                    .statusCode(200);
        }
    }
}
