package org.rostilos.codecrow.webserver;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import static io.restassured.RestAssured.given;
import static org.hamcrest.Matchers.*;

/**
 * Integration tests for LlmModelController.
 * Tests model search, status, sync (admin-only), and custom model fetch.
 */
class LlmModelControllerIT extends BaseWebServerIT {

    @Nested
    @DisplayName("GET /api/llm-models")
    class SearchModels {

        @Test
        @DisplayName("Search models — unauthenticated — returns result or 401")
        void searchModels_unauthenticated() {
            // Models endpoint may or may not require auth
            unauthenticatedRequest()
            .when()
                .get("/api/llm-models")
            .then()
                .statusCode(anyOf(is(200), is(401)));
        }

        @Test
        @DisplayName("Search models with provider filter — authenticated")
        void searchModels_withProviderFilter() {
            createTestUser("llmuser", "llm@example.com", "password123");

            authenticatedRequest("llmuser")
                .queryParam("provider", "OPENAI")
                .queryParam("page", 0)
                .queryParam("size", 10)
            .when()
                .get("/api/llm-models")
            .then()
                .statusCode(200);
        }

        @Test
        @DisplayName("Search models with search query — paginated")
        void searchModels_withSearch() {
            createTestUser("llmsearchuser", "llmsearch@example.com", "password123");

            authenticatedRequest("llmsearchuser")
                .queryParam("search", "gpt")
                .queryParam("page", 0)
                .queryParam("size", 5)
            .when()
                .get("/api/llm-models")
            .then()
                .statusCode(200)
                .body("page", equalTo(0))
                .body("pageSize", equalTo(5));
        }
    }

    @Nested
    @DisplayName("GET /api/llm-models/status")
    class ModelStatus {

        @Test
        @DisplayName("Get model status — returns availability info")
        void getStatus_returnsAvailability() {
            createTestUser("statususer", "status@example.com", "password123");

            authenticatedRequest("statususer")
            .when()
                .get("/api/llm-models/status")
            .then()
                .statusCode(200)
                .body("hasModels", notNullValue());
        }
    }

    @Nested
    @DisplayName("POST /api/llm-models/sync")
    class SyncModels {

        @Test
        @DisplayName("Sync models — non-admin — 403")
        void syncModels_nonAdmin_returns403() {
            createTestUser("nonadmin", "nonadmin@example.com", "password123");

            authenticatedRequest("nonadmin")
            .when()
                .post("/api/llm-models/sync")
            .then()
                .statusCode(403);
        }

        @Test
        @DisplayName("Sync models — admin — succeeds or fails gracefully")
        void syncModels_admin_executes() {
            createAdminUser("syncadmin", "syncadmin@example.com", "password123");

            authenticatedRequest("syncadmin")
            .when()
                .post("/api/llm-models/sync")
            .then()
                // May succeed or return 500 if no external connectivity
                .statusCode(anyOf(is(200), is(500)));
        }

        @Test
        @DisplayName("Sync models — unauthenticated — 401")
        void syncModels_unauthenticated_returns401() {
            unauthenticatedRequest()
            .when()
                .post("/api/llm-models/sync")
            .then()
                .statusCode(401);
        }
    }

    @Nested
    @DisplayName("POST /api/llm-models/fetch-custom")
    class FetchCustomModels {

        @Test
        @DisplayName("Fetch custom models — authenticated — requires valid base URL")
        void fetchCustomModels_authenticated() {
            createTestUser("customuser", "custom@example.com", "password123");

            authenticatedRequest("customuser")
                .body("""
                    {
                        "baseUrl": "https://localhost:11434",
                        "apiKey": "test-key"
                    }
                    """)
            .when()
                .post("/api/llm-models/fetch-custom")
            .then()
                // Will fail to connect but should not be a 401/403
                .statusCode(anyOf(is(200), is(400), is(500)));
        }

        @Test
        @DisplayName("Fetch custom models — unauthenticated — 401")
        void fetchCustomModels_unauthenticated_returns401() {
            unauthenticatedRequest()
                .body("""
                    {
                        "baseUrl": "http://localhost:11434",
                        "apiKey": "test-key"
                    }
                    """)
            .when()
                .post("/api/llm-models/fetch-custom")
            .then()
                .statusCode(401);
        }
    }
}
