package org.rostilos.codecrow.pipelineagent;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import static io.restassured.RestAssured.given;
import static org.hamcrest.Matchers.*;

/**
 * Integration tests for RagIndexingController.
 * RAG endpoints require project-level JWT authentication.
 */
class RagIndexingControllerIT extends BasePipelineAgentIT {

    @Nested
    @DisplayName("POST /api/rag/index")
    class TriggerIndexing {

        @Test
        @DisplayName("RAG indexing — unauthenticated — 401")
        void ragIndex_noAuth_returns401() {
            unauthenticatedRequest()
                .body("""
                    {
                        "branch": "main"
                    }
                    """)
            .when()
                .post("/api/rag/index")
            .then()
                .statusCode(401);
        }

        @Test
        @DisplayName("RAG indexing — invalid JWT — 401")
        void ragIndex_invalidToken_returns401() {
            given()
                .header("Authorization", "Bearer invalid.jwt.here")
                .contentType("application/json")
                .body("""
                    {
                        "branch": "main"
                    }
                    """)
            .when()
                .post("/api/rag/index")
            .then()
                .statusCode(401);
        }

        @Test
        @DisplayName("RAG indexing — non-existent project — 404")
        void ragIndex_nonExistentProject_returns404() {
            projectAuthRequest(77777L)
                .body("""
                    {
                        "branch": "main"
                    }
                    """)
            .when()
                .post("/api/rag/index")
            .then()
                .statusCode(404);
        }

        @Test
        @DisplayName("RAG indexing — valid project auth — streams SSE or processes")
        void ragIndex_validAuth_returnsStreamOrError() {
            Long projectId = createTestProject("rag-idx-ns", "RAG Index Project");

            projectAuthRequest(projectId)
                .body("""
                    {
                        "branch": "main"
                    }
                    """)
            .when()
                .post("/api/rag/index")
            .then()
                // The endpoint streams SSE; may succeed or fail depending on
                // external RAG pipeline connectivity, but auth should pass
                .statusCode(anyOf(is(200), is(400), is(500)));
        }
    }

    @Nested
    @DisplayName("GET /api/rag/can-index/{projectId}")
    class CanStartIndexing {

        @Test
        @DisplayName("Can-index check — unauthenticated — 401")
        void canIndex_noAuth_returns401() {
            unauthenticatedRequest()
            .when()
                .get("/api/rag/can-index/1")
            .then()
                .statusCode(401);
        }

        @Test
        @DisplayName("Can-index check — valid auth — returns status")
        void canIndex_validAuth_returnsStatus() {
            Long projectId = createTestProject("rag-check-ns", "RAG Check Project");

            projectAuthRequest(projectId)
            .when()
                .get("/api/rag/can-index/" + projectId)
            .then()
                .statusCode(200)
                .body("canIndex", notNullValue());
        }

        @Test
        @DisplayName("Can-index check — non-existent project in JWT — 404")
        void canIndex_nonExistentProject_returns404() {
            projectAuthRequest(66666L)
            .when()
                .get("/api/rag/can-index/66666")
            .then()
                .statusCode(404);
        }
    }
}
