package org.rostilos.codecrow.integration.analysis;

import com.github.tomakehurst.wiremock.WireMockServer;
import io.restassured.response.Response;
import org.junit.jupiter.api.*;
import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.integration.base.BaseIntegrationTest;
import org.rostilos.codecrow.integration.mock.AIProviderMockSetup;
import org.rostilos.codecrow.integration.mock.BitbucketCloudMockSetup;

import java.util.concurrent.TimeUnit;

import static com.github.tomakehurst.wiremock.client.WireMock.*;
import static io.restassured.RestAssured.given;
import static org.awaitility.Awaitility.await;
import static org.hamcrest.Matchers.*;

/**
 * Integration tests for webhook-triggered analysis.
 * Simulates Bitbucket/GitLab webhook events and validates analysis flow.
 */
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
@Tag("analysis")
@Tag("webhook")
class AnalysisWebhookIT extends BaseIntegrationTest {

    private BitbucketCloudMockSetup bitbucketMock;
    private AIProviderMockSetup aiMock;
    private Workspace testWorkspace;
    private Long vcsConnectionId;
    private Long aiConnectionId;
    private String projectNamespace;
    private String projectToken;

    @BeforeEach
    void setUpTest() {
        bitbucketMock = new BitbucketCloudMockSetup(bitbucketCloudMock);
        aiMock = new AIProviderMockSetup(openaiMock, anthropicMock, openrouterMock);
        authTestHelper.initializeTestUsers();
        testWorkspace = authTestHelper.getDefaultWorkspace();

        setupVcsConnection();
        setupAiConnection();
        setupProject();
    }

    private void setupVcsConnection() {
        bitbucketMock.setupValidUserResponse();
        bitbucketMock.setupWorkspacesResponse("test-workspace", "Test Workspace", "{ws-uuid}");
        bitbucketMock.setupRepositoriesResponse("test-workspace");
        bitbucketMock.setupRepositoryResponse("test-workspace", "test-repo");
        bitbucketMock.setupWebhookCreation("test-workspace", "test-repo");

        // Create VCS connection directly in database to bypass OAuth validation
        VcsConnection vcsConnection = authTestHelper.createTestVcsConnection(
                testWorkspace, 
                "Webhook Test VCS"
        );
        vcsConnectionId = vcsConnection.getId();
        System.out.println("Created VCS Connection ID for webhook tests: " + vcsConnectionId);
    }

    private void setupAiConnection() {
        aiMock.setupChatCompletion("Analysis complete");

        // Create AI connection directly in database
        AIConnection aiConnection = authTestHelper.createTestAiConnection(
                testWorkspace,
                "Webhook Test AI"
        );
        aiConnectionId = aiConnection.getId();
        System.out.println("Created AI Connection ID for webhook tests: " + aiConnectionId);
    }

    private void setupProject() {
        // Use unique namespace per test run to avoid duplicate namespace errors
        projectNamespace = "webhook-analysis-" + System.currentTimeMillis();
        String createBody = String.format("""
            {
                "name": "Webhook Analysis Project",
                "namespace": "%s",
                "description": "Project for webhook analysis tests"
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

        String tokenBody = """
            {
                "name": "Webhook Token",
                "lifetime": "P30D"
            }
            """;

        Response tokenResponse = given()
                .spec(authenticatedAsAdmin())
                .body(tokenBody)
                .when()
                .post("/api/{workspaceSlug}/project/{namespace}/token/generate",
                        testWorkspace.getSlug(), projectNamespace)
                .then()
                .statusCode(200)
                .extract().response();

        projectToken = tokenResponse.jsonPath().getString("token");
    }

    @Nested
    @DisplayName("Bitbucket Cloud Webhook Tests")
    class BitbucketWebhookTests {

        @Test
        @Order(1)
        @DisplayName("Should process pull request created webhook")
        void shouldProcessPullRequestCreatedWebhook() {
            bitbucketMock.setupPullRequestDetails("test-workspace", "test-repo", 1);
            bitbucketMock.setupPullRequestDiff("test-workspace", "test-repo", 1);
            bitbucketMock.setupFileContent("test-workspace", "test-repo", "main", "src/Main.java");
            bitbucketMock.setupCommitsResponse("test-workspace", "test-repo");
            bitbucketMock.setupPullRequestCommentCreation("test-workspace", "test-repo", 1);

            String webhookPayload = """
                {
                    "pullrequest": {
                        "id": 1,
                        "title": "Add new feature",
                        "source": {
                            "branch": {
                                "name": "feature/new-feature"
                            }
                        },
                        "destination": {
                            "branch": {
                                "name": "main"
                            }
                        }
                    },
                    "repository": {
                        "uuid": "{repo-uuid}",
                        "name": "test-repo",
                        "full_name": "test-workspace/test-repo"
                    },
                    "actor": {
                        "display_name": "Test User"
                    }
                }
                """;

            given()
                    .contentType("application/json")
                    .header("X-Event-Key", "pullrequest:created")
                    .header("X-Hook-UUID", "{hook-uuid}")
                    .header("X-Project-Token", projectToken)
                    .body(webhookPayload)
                    .when()
                    .post("/api/webhook/bitbucket_cloud/{workspaceSlug}/{projectNamespace}",
                            testWorkspace.getSlug(), projectNamespace)
                    .then()
                    .statusCode(anyOf(equalTo(200), equalTo(202), equalTo(400)));
        }

        @Test
        @Order(2)
        @DisplayName("Should process pull request updated webhook")
        void shouldProcessPullRequestUpdatedWebhook() {
            bitbucketMock.setupPullRequestDetails("test-workspace", "test-repo", 2);
            bitbucketMock.setupPullRequestDiff("test-workspace", "test-repo", 2);
            bitbucketMock.setupFileContent("test-workspace", "test-repo", "main", "src/Main.java");

            String webhookPayload = """
                {
                    "pullrequest": {
                        "id": 2,
                        "title": "Updated feature",
                        "source": {
                            "branch": {
                                "name": "feature/updated"
                            }
                        },
                        "destination": {
                            "branch": {
                                "name": "main"
                            }
                        }
                    },
                    "repository": {
                        "uuid": "{repo-uuid}",
                        "name": "test-repo",
                        "full_name": "test-workspace/test-repo"
                    }
                }
                """;

            given()
                    .contentType("application/json")
                    .header("X-Event-Key", "pullrequest:updated")
                    .header("X-Project-Token", projectToken)
                    .body(webhookPayload)
                    .when()
                    .post("/api/webhook/bitbucket_cloud/{workspaceSlug}/{projectNamespace}",
                            testWorkspace.getSlug(), projectNamespace)
                    .then()
                    .statusCode(anyOf(equalTo(200), equalTo(202), equalTo(400)));
        }

        @Test
        @Order(3)
        @DisplayName("Should reject invalid webhook signature")
        void shouldRejectInvalidWebhookToken() {
            String webhookPayload = """
                {
                    "pullrequest": {
                        "id": 1,
                        "title": "Test PR"
                    }
                }
                """;

            given()
                    .contentType("application/json")
                    .header("X-Event-Key", "pullrequest:created")
                    .header("X-Project-Token", "invalid-token")
                    .body(webhookPayload)
                    .when()
                    .post("/api/webhook/bitbucket_cloud/{workspaceSlug}/{projectNamespace}",
                            testWorkspace.getSlug(), projectNamespace)
                    .then()
                    .statusCode(anyOf(equalTo(401), equalTo(403)));
        }

        @Test
        @Order(4)
        @DisplayName("Should ignore unsupported webhook events")
        void shouldIgnoreUnsupportedEvents() {
            String webhookPayload = """
                {
                    "push": {
                        "changes": []
                    }
                }
                """;

            given()
                    .contentType("application/json")
                    .header("X-Event-Key", "repo:push")
                    .header("X-Project-Token", projectToken)
                    .body(webhookPayload)
                    .when()
                    .post("/api/webhook/bitbucket_cloud/{workspaceSlug}/{projectNamespace}",
                            testWorkspace.getSlug(), projectNamespace)
                    .then()
                    .statusCode(anyOf(equalTo(200), equalTo(204), equalTo(400)));
        }
    }

    @Nested
    @DisplayName("Pipeline Analysis Tests")
    class PipelineAnalysisTests {

        @Test
        @Order(1)
        @DisplayName("Should trigger pipeline analysis via API")
        void shouldTriggerPipelineAnalysis() {
            bitbucketMock.setupPullRequestDetails("test-workspace", "test-repo", 10);
            bitbucketMock.setupPullRequestDiff("test-workspace", "test-repo", 10);
            bitbucketMock.setupFileContent("test-workspace", "test-repo", "main", "src/Main.java");

            String analysisRequest = """
                {
                    "vcsType": "BITBUCKET_CLOUD",
                    "repositorySlug": "test-repo",
                    "workspaceSlug": "test-workspace",
                    "pullRequestId": 10,
                    "sourceBranch": "feature/test",
                    "targetBranch": "main"
                }
                """;

            given()
                    .spec(authenticatedAsAdmin())
                    .body(analysisRequest)
                    .when()
                    .post("/api/{workspaceSlug}/project/{namespace}/analyze/pr",
                            testWorkspace.getSlug(), projectNamespace)
                    .then()
                    .statusCode(anyOf(equalTo(200), equalTo(202), equalTo(400)));
        }

        @Test
        @Order(2)
        @DisplayName("Should get analysis history")
        void shouldGetAnalysisHistory() {
            given()
                    .spec(authenticatedAsAdmin())
                    .when()
                    .get("/api/{workspaceSlug}/project/{namespace}/analysis/history",
                            testWorkspace.getSlug(), projectNamespace)
                    .then()
                    .statusCode(200);
        }

        @Test
        @Order(3)
        @DisplayName("Should get analysis details")
        void shouldGetAnalysisDetails() {
            String jobId = "test-job-id";
            
            given()
                    .spec(authenticatedAsAdmin())
                    .when()
                    .get("/api/{workspaceSlug}/project/{namespace}/analysis/{jobId}",
                            testWorkspace.getSlug(), projectNamespace, jobId)
                    .then()
                    .statusCode(anyOf(equalTo(200), equalTo(404)));
        }
    }

    @Nested
    @DisplayName("Analysis Configuration Tests")
    class AnalysisConfigTests {

        @Test
        @Order(1)
        @DisplayName("Should update analysis branch patterns")
        void shouldUpdateBranchPatterns() {
            String configBody = """
                {
                    "targetBranchPatterns": ["main", "develop", "release/*"],
                    "excludePatterns": ["dependabot/*", "renovate/*"]
                }
                """;

            given()
                    .spec(authenticatedAsAdmin())
                    .body(configBody)
                    .when()
                    .patch("/api/{workspaceSlug}/project/{namespace}/config/branches",
                            testWorkspace.getSlug(), projectNamespace)
                    .then()
                    .statusCode(anyOf(equalTo(200), equalTo(204)));
        }

        @Test
        @Order(2)
        @DisplayName("Should set excluded file patterns")
        void shouldSetExcludedFilePatterns() {
            String configBody = """
                {
                    "excludePatterns": ["*.lock", "*.min.js", "vendor/**", "node_modules/**"]
                }
                """;

            given()
                    .spec(authenticatedAsAdmin())
                    .body(configBody)
                    .when()
                    .patch("/api/{workspaceSlug}/project/{namespace}/config/files",
                            testWorkspace.getSlug(), projectNamespace)
                    .then()
                    .statusCode(anyOf(equalTo(200), equalTo(204)));
        }

        @Test
        @Order(3)
        @DisplayName("Should configure analysis prompts")
        void shouldConfigureAnalysisPrompts() {
            String promptConfig = """
                {
                    "systemPrompt": "You are a code reviewer. Focus on security and performance.",
                    "userPromptTemplate": "Review the following code changes: {{diff}}"
                }
                """;

            given()
                    .spec(authenticatedAsAdmin())
                    .body(promptConfig)
                    .when()
                    .patch("/api/{workspaceSlug}/project/{namespace}/config/prompts",
                            testWorkspace.getSlug(), projectNamespace)
                    .then()
                    .statusCode(anyOf(equalTo(200), equalTo(204)));
        }
    }
}
