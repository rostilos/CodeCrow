package org.rostilos.codecrow.pipelineagent;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static io.restassured.RestAssured.given;
import static org.hamcrest.Matchers.*;

/**
 * Integration tests for pipeline-agent HealthCheckController.
 * Health endpoint is public — no authentication required.
 */
class HealthCheckControllerIT extends BasePipelineAgentIT {

    @Test
    @DisplayName("Health endpoint returns OK")
    void healthEndpoint_returnsOk() {
        given()
        .when()
            .get("/actuator/health")
        .then()
            .statusCode(200)
            .body(equalTo("OK"));
    }

    @Test
    @DisplayName("Health endpoint is accessible without auth")
    void healthEndpoint_noAuth_accessible() {
        unauthenticatedRequest()
        .when()
            .get("/actuator/health")
        .then()
            .statusCode(200);
    }

    @Test
    @DisplayName("Health endpoint accepts any request method context")
    void healthEndpoint_withInvalidToken_stillAccessible() {
        given()
            .header("Authorization", "Bearer invalid-token")
        .when()
            .get("/actuator/health")
        .then()
            .statusCode(200)
            .body(equalTo("OK"));
    }
}
