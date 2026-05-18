package org.rostilos.codecrow.webserver;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static io.restassured.RestAssured.given;
import static org.hamcrest.Matchers.*;

/**
 * Integration tests for PublicSiteConfigController.
 * All endpoints are public — no authentication required.
 */
class PublicSiteConfigControllerIT extends BaseWebServerIT {

    @Test
    @DisplayName("Public site config returns vcsProviders key")
    void getPublicConfig_returnsVcsProviders() {
        unauthenticatedRequest()
        .when()
            .get("/api/public/site-config")
        .then()
            .statusCode(200)
            .body("vcsProviders", notNullValue());
    }

    @Test
    @DisplayName("Public site config is accessible without auth")
    void getPublicConfig_noAuth_returns200() {
        given()
        .when()
            .get("/api/public/site-config")
        .then()
            .statusCode(200);
    }

    @Test
    @DisplayName("Public site config returns JSON content type")
    void getPublicConfig_returnsJson() {
        unauthenticatedRequest()
        .when()
            .get("/api/public/site-config")
        .then()
            .statusCode(200)
            .contentType(containsString("application/json"));
    }
}
