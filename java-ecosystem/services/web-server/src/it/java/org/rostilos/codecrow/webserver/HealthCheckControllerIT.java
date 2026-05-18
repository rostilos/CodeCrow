package org.rostilos.codecrow.webserver;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static io.restassured.RestAssured.given;
import static org.hamcrest.Matchers.equalTo;

/**
 * Integration tests for the HealthCheck controller.
 * <p>Verifies the /actuator/health endpoint is publicly accessible
 * and returns the expected response.</p>
 */
class HealthCheckControllerIT extends BaseWebServerIT {

    @Test
    @DisplayName("GET /actuator/health returns OK without authentication")
    void healthEndpoint_returnsOk() {
        unauthenticatedRequest()
            .when()
                .get("/actuator/health")
            .then()
                .statusCode(200)
                .body(equalTo("OK"));
    }

    @Test
    @DisplayName("GET /actuator/health returns OK even with invalid JWT")
    void healthEndpoint_acceptsInvalidToken() {
        given()
            .header("Authorization", "Bearer invalid-jwt-token")
        .when()
            .get("/actuator/health")
        .then()
            .statusCode(200)
            .body(equalTo("OK"));
    }
}
