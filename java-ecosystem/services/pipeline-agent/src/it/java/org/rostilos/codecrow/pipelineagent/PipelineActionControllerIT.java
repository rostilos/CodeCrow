package org.rostilos.codecrow.pipelineagent;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import static io.restassured.RestAssured.given;
import static org.hamcrest.Matchers.*;

/**
 * Integration tests for ProviderPipelineActionController.
 * Processing endpoints require project-level JWT authentication.
 * The JWT subject must be the project ID, and the request body's
 * projectId must match the JWT's subject.
 */
class PipelineActionControllerIT extends BasePipelineAgentIT {

    @Nested
    @DisplayName("POST /api/processing/webhook/pr")
    class PrProcessing {

        @Test
        @DisplayName("PR processing — unauthenticated — 401")
        void prProcessing_noAuth_returns401() {
            unauthenticatedRequest()
                .body("""
                    {
                        "projectId": 1,
                        "pullRequestId": 42,
                        "sourceBranch": "feature/test",
                        "targetBranch": "main",
                        "commitHash": "abc123",
                        "rawDiff": "diff content",
                        "changedFiles": []
                    }
                    """)
            .when()
                .post("/api/processing/webhook/pr")
            .then()
                .statusCode(401);
        }

        @Test
        @DisplayName("PR processing — invalid JWT — 401")
        void prProcessing_invalidToken_returns401() {
            given()
                .header("Authorization", "Bearer invalid.jwt.token")
                .contentType("application/json")
                .body("""
                    {
                        "projectId": 1,
                        "pullRequestId": 42,
                        "sourceBranch": "feature/test",
                        "targetBranch": "main"
                    }
                    """)
            .when()
                .post("/api/processing/webhook/pr")
            .then()
                .statusCode(401);
        }

        @Test
        @DisplayName("PR processing — JWT for non-existent project — 404")
        void prProcessing_nonExistentProject_returns404() {
            projectAuthRequest(99999L)
                .body("""
                    {
                        "projectId": 99999,
                        "pullRequestId": 42,
                        "sourceBranch": "feature/test",
                        "targetBranch": "main"
                    }
                    """)
            .when()
                .post("/api/processing/webhook/pr")
            .then()
                .statusCode(404);
        }

        @Test
        @DisplayName("PR processing — project ID mismatch — 401")
        void prProcessing_projectIdMismatch_returns401() {
            Long projectId = createTestProject("pr-mismatch-ns", "Mismatch Project");

            // JWT subject is projectId but body says different projectId
            projectAuthRequest(projectId)
                .body("""
                    {
                        "projectId": 99999,
                        "pullRequestId": 42,
                        "sourceBranch": "feature/test",
                        "targetBranch": "main"
                    }
                    """)
            .when()
                .post("/api/processing/webhook/pr")
            .then()
                .statusCode(401);
        }

        @Test
        @DisplayName("PR processing — valid auth, valid project — accepted or processes")
        void prProcessing_validAuth_accepted() {
            Long projectId = createTestProject("pr-valid-ns", "Valid PR Project");

            projectAuthRequest(projectId)
                .body("""
                    {
                        "projectId": %d,
                        "pullRequestId": 42,
                        "sourceBranch": "feature/test",
                        "targetBranch": "main",
                        "commitHash": "abc123def456",
                        "rawDiff": "diff --git a/file.txt b/file.txt",
                        "changedFiles": [{"path": "file.txt", "status": "modified"}]
                    }
                    """.formatted(projectId))
            .when()
                .post("/api/processing/webhook/pr")
            .then()
                // The endpoint returns streaming NDJSON; may succeed or fail
                // depending on external service availability, but auth should pass
                .statusCode(anyOf(is(200), is(400), is(500)));
        }
    }

    @Nested
    @DisplayName("POST /api/processing/webhook/branch")
    class BranchProcessing {

        @Test
        @DisplayName("Branch processing — unauthenticated — 401")
        void branchProcessing_noAuth_returns401() {
            unauthenticatedRequest()
                .body("""
                    {
                        "projectId": 1,
                        "branchName": "main",
                        "commitHash": "abc123"
                    }
                    """)
            .when()
                .post("/api/processing/webhook/branch")
            .then()
                .statusCode(401);
        }

        @Test
        @DisplayName("Branch processing — invalid JWT — 401")
        void branchProcessing_invalidToken_returns401() {
            given()
                .header("Authorization", "Bearer not-a-valid-jwt")
                .contentType("application/json")
                .body("""
                    {
                        "projectId": 1,
                        "branchName": "main"
                    }
                    """)
            .when()
                .post("/api/processing/webhook/branch")
            .then()
                .statusCode(401);
        }

        @Test
        @DisplayName("Branch processing — JWT for non-existent project — 404")
        void branchProcessing_nonExistentProject_returns404() {
            projectAuthRequest(88888L)
                .body("""
                    {
                        "projectId": 88888,
                        "branchName": "main"
                    }
                    """)
            .when()
                .post("/api/processing/webhook/branch")
            .then()
                .statusCode(404);
        }

        @Test
        @DisplayName("Branch processing — valid auth — accepted or processes")
        void branchProcessing_validAuth_accepted() {
            Long projectId = createTestProject("branch-valid-ns", "Valid Branch Project");

            projectAuthRequest(projectId)
                .body("""
                    {
                        "projectId": %d,
                        "branchName": "main",
                        "commitHash": "def456abc789"
                    }
                    """.formatted(projectId))
            .when()
                .post("/api/processing/webhook/branch")
            .then()
                .statusCode(anyOf(is(200), is(400), is(500)));
        }
    }
}
