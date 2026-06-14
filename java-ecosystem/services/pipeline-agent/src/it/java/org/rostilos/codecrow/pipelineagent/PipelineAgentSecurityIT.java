package org.rostilos.codecrow.pipelineagent;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import static io.restassured.RestAssured.given;
import static org.hamcrest.Matchers.*;

/**
 * Integration tests for pipeline-agent security layer.
 * Verifies that the ProjectInternalJwtFilter correctly
 * enforces authentication on protected endpoints.
 */
class PipelineAgentSecurityIT extends BasePipelineAgentIT {

    @Nested
    @DisplayName("Public endpoints — no auth required")
    class PublicEndpoints {

        @Test
        @DisplayName("Health endpoint is accessible without auth")
        void healthEndpoint_noAuth_returns200() {
            unauthenticatedRequest()
            .when()
                .get("/actuator/health")
            .then()
                .statusCode(200);
        }

        @Test
        @DisplayName("Webhook endpoint is accessible without auth")
        void webhookEndpoint_noAuth_accessible() {
            // Even though it may fail to process, it should not return 401
            given()
                .contentType("application/json")
                .body("{\"test\": true}")
            .when()
                .post("/api/webhooks/github")
            .then()
                .statusCode(not(401));
        }

        @Test
        @DisplayName("Webhook with auth token is accessible without JWT")
        void webhookWithToken_noJwt_accessible() {
            given()
                .contentType("application/json")
                .body("{\"test\": true}")
            .when()
                .post("/api/webhooks/github/some-token")
            .then()
                .statusCode(not(401));
        }
    }

    @Nested
    @DisplayName("Protected endpoints — JWT required")
    class ProtectedEndpoints {

        @Test
        @DisplayName("Processing endpoint without auth — 401")
        void processingEndpoint_noAuth_returns401() {
            unauthenticatedRequest()
                .body("{\"projectId\": 1}")
            .when()
                .post("/api/processing/webhook/pr")
            .then()
                .statusCode(401);
        }

        @Test
        @DisplayName("RAG endpoint without auth — 401")
        void ragEndpoint_noAuth_returns401() {
            unauthenticatedRequest()
                .body("{\"branch\": \"main\"}")
            .when()
                .post("/api/rag/index")
            .then()
                .statusCode(401);
        }

        @Test
        @DisplayName("Processing endpoint with expired/invalid JWT — 401")
        void processingEndpoint_invalidJwt_returns401() {
            given()
                .header("Authorization", "Bearer eyJhbGciOiJIUzI1NiJ9.invalid.payload")
                .contentType("application/json")
                .body("{\"projectId\": 1}")
            .when()
                .post("/api/processing/webhook/pr")
            .then()
                .statusCode(401);
        }

        @Test
        @DisplayName("RAG can-index endpoint without auth — 401")
        void ragCanIndex_noAuth_returns401() {
            unauthenticatedRequest()
            .when()
                .get("/api/rag/can-index/1")
            .then()
                .statusCode(401);
        }

        @Test
        @DisplayName("Processing endpoint with valid JWT for existing project — not 401")
        void processingEndpoint_validAuth_notUnauthorized() {
            Long projectId = createTestProject("sec-test-ns", "Security Test Project");

            projectAuthRequest(projectId)
                .body("""
                    {
                        "projectId": %d,
                        "pullRequestId": 42,
                        "targetBranchName": "main",
                        "sourceBranchName": "feature/security-auth-smoke",
                        "commitHash": "abc123def456",
                        "analysisType": "PR_REVIEW"
                    }
                    """.formatted(projectId))
            .when()
                .post("/api/processing/webhook/pr")
            .then()
                // Downstream processing may still fail in this security smoke test,
                // but a valid project JWT must pass the auth filter.
                .statusCode(not(401));
        }
    }
}
