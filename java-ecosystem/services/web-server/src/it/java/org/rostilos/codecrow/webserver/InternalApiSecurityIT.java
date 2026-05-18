package org.rostilos.codecrow.webserver;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static io.restassured.RestAssured.given;
import static org.hamcrest.Matchers.*;

/**
 * Integration tests for internal API security.
 * <p>Verifies the X-Internal-Secret header-based authentication
 * for internal service-to-service endpoints.</p>
 */
class InternalApiSecurityIT extends BaseWebServerIT {

    @Test
    @DisplayName("Internal endpoint with valid secret — accessible")
    void internalEndpoint_validSecret_accessible() {
        internalRequest()
        .when()
            .get("/internal/projects")
        .then()
            .statusCode(anyOf(is(200), is(404))); // Endpoint exists but may have no data
    }

    @Test
    @DisplayName("Internal endpoint without secret — 401")
    void internalEndpoint_noSecret_unauthorized() {
        unauthenticatedRequest()
        .when()
            .get("/internal/projects")
        .then()
            .statusCode(401);
    }

    @Test
    @DisplayName("Internal endpoint with wrong secret — 401")
    void internalEndpoint_wrongSecret_unauthorized() {
        given()
            .header("X-Internal-Secret", "wrong-secret-value")
        .when()
            .get("/internal/projects")
        .then()
            .statusCode(401);
    }

    @Test
    @DisplayName("Internal endpoint with JWT instead of secret — 401")
    void internalEndpoint_jwtInsteadOfSecret_unauthorized() {
        // JWT auth should not work for internal endpoints
        Long userId = createTestUser("intuser", "int@example.com", "password123");
        authenticatedRequest("intuser")
        .when()
            .get("/internal/projects")
        .then()
            .statusCode(401);
    }
}
