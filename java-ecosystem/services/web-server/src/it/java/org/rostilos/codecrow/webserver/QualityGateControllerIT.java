package org.rostilos.codecrow.webserver;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import static io.restassured.RestAssured.given;
import static org.hamcrest.Matchers.*;

/**
 * Integration tests for QualityGateController.
 * Quality gates are workspace-scoped and require workspace membership/ownership.
 */
class QualityGateControllerIT extends BaseWebServerIT {

    private String workspaceSlug;

    @BeforeEach
    void setupWorkspace() {
        createTestUser("qgowner", "qgowner@example.com", "password123");

        workspaceSlug = "qg-test-ws";
        authenticatedRequest("qgowner")
            .body("""
                {
                    "slug": "%s",
                    "name": "QG Test Workspace"
                }
                """.formatted(workspaceSlug))
        .when()
            .post("/api/workspace/create")
        .then()
            .statusCode(201);
    }

    // ───────────────────────────────────────────────
    // List Quality Gates
    // ───────────────────────────────────────────────
    @Nested
    @DisplayName("GET /api/{workspace}/quality-gates")
    class ListQualityGates {

        @Test
        @DisplayName("List quality gates — returns default gate auto-created")
        void listQualityGates_returnsDefaultGate() {
            authenticatedRequest("qgowner")
            .when()
                .get("/api/" + workspaceSlug + "/quality-gates")
            .then()
                .statusCode(200)
                .body("$", hasSize(greaterThanOrEqualTo(1)));
        }

        @Test
        @DisplayName("Unauthenticated — 401")
        void listQualityGates_unauthenticated_returns401() {
            unauthenticatedRequest()
            .when()
                .get("/api/" + workspaceSlug + "/quality-gates")
            .then()
                .statusCode(401);
        }

        @Test
        @DisplayName("Non-member — 403")
        void listQualityGates_nonMember_returns403() {
            createTestUser("qgstranger", "qgstranger@example.com", "password123");

            authenticatedRequest("qgstranger")
            .when()
                .get("/api/" + workspaceSlug + "/quality-gates")
            .then()
                .statusCode(403);
        }
    }

    // ───────────────────────────────────────────────
    // Get Default Quality Gate
    // ───────────────────────────────────────────────
    @Nested
    @DisplayName("GET /api/{workspace}/quality-gates/default")
    class GetDefaultQualityGate {

        @Test
        @DisplayName("Get default — auto-created if missing")
        void getDefault_returnsGate() {
            authenticatedRequest("qgowner")
            .when()
                .get("/api/" + workspaceSlug + "/quality-gates/default")
            .then()
                .statusCode(200)
                .body("isDefault", is(true))
                .body("name", notNullValue());
        }
    }

    // ───────────────────────────────────────────────
    // Create Quality Gate
    // ───────────────────────────────────────────────
    @Nested
    @DisplayName("POST /api/{workspace}/quality-gates")
    class CreateQualityGate {

        @Test
        @DisplayName("Create quality gate with conditions — 201")
        void createQualityGate_valid_returns201() {
            authenticatedRequest("qgowner")
                .body("""
                    {
                        "name": "Strict Gate",
                        "description": "No critical issues allowed",
                        "conditions": [
                            {
                                "metric": "ISSUES_BY_SEVERITY",
                                "severity": "HIGH",
                                "comparator": "GREATER_THAN",
                                "thresholdValue": 0,
                                "enabled": true
                            }
                        ]
                    }
                    """)
            .when()
                .post("/api/" + workspaceSlug + "/quality-gates")
            .then()
                .statusCode(201)
                .body("name", equalTo("Strict Gate"))
                .body("description", equalTo("No critical issues allowed"))
                .body("conditions", hasSize(1));
        }

        @Test
        @DisplayName("Create quality gate without conditions — 400")
        void createQualityGate_noConditions_returns400() {
            authenticatedRequest("qgowner")
                .body("""
                    {
                        "name": "Empty Gate",
                        "conditions": []
                    }
                    """)
            .when()
                .post("/api/" + workspaceSlug + "/quality-gates")
            .then()
                .statusCode(400);
        }

        @Test
        @DisplayName("Create quality gate without name — 400")
        void createQualityGate_noName_returns400() {
            authenticatedRequest("qgowner")
                .body("""
                    {
                        "conditions": [
                            {
                                "metric": "ISSUES_BY_SEVERITY",
                                "severity": "HIGH",
                                "comparator": "GREATER_THAN",
                                "thresholdValue": 0
                            }
                        ]
                    }
                    """)
            .when()
                .post("/api/" + workspaceSlug + "/quality-gates")
            .then()
                .statusCode(400);
        }

        @Test
        @DisplayName("Non-member cannot create — 403")
        void createQualityGate_nonMember_returns403() {
            createTestUser("qgnonmember", "qgnonmember@example.com", "password123");

            authenticatedRequest("qgnonmember")
                .body("""
                    {
                        "name": "Hacker Gate",
                        "conditions": [
                            {
                                "metric": "ISSUES_BY_SEVERITY",
                                "severity": "HIGH",
                                "comparator": "GREATER_THAN",
                                "thresholdValue": 0
                            }
                        ]
                    }
                    """)
            .when()
                .post("/api/" + workspaceSlug + "/quality-gates")
            .then()
                .statusCode(403);
        }
    }

    // ───────────────────────────────────────────────
    // Update Quality Gate
    // ───────────────────────────────────────────────
    @Nested
    @DisplayName("PUT /api/{workspace}/quality-gates/{id}")
    class UpdateQualityGate {

        @Test
        @DisplayName("Update quality gate name and description")
        void updateQualityGate_validData_succeeds() {
            // Create a quality gate first
            int gateId = authenticatedRequest("qgowner")
                .body("""
                    {
                        "name": "Update Target",
                        "conditions": [
                            {
                                "metric": "ISSUES_BY_SEVERITY",
                                "severity": "HIGH",
                                "comparator": "GREATER_THAN",
                                "thresholdValue": 0
                            }
                        ]
                    }
                    """)
            .when()
                .post("/api/" + workspaceSlug + "/quality-gates")
            .then()
                .statusCode(201)
                .extract().path("id");

            // Update it
            authenticatedRequest("qgowner")
                .body("""
                    {
                        "name": "Updated Gate Name",
                        "description": "Updated description"
                    }
                    """)
            .when()
                .put("/api/" + workspaceSlug + "/quality-gates/" + gateId)
            .then()
                .statusCode(200)
                .body("name", equalTo("Updated Gate Name"))
                .body("description", equalTo("Updated description"));
        }
    }

    // ───────────────────────────────────────────────
    // Set Default Quality Gate
    // ───────────────────────────────────────────────
    @Nested
    @DisplayName("POST /api/{workspace}/quality-gates/{id}/set-default")
    class SetDefaultQualityGate {

        @Test
        @DisplayName("Set a gate as default")
        void setDefault_succeeds() {
            int gateId = authenticatedRequest("qgowner")
                .body("""
                    {
                        "name": "New Default Gate",
                        "conditions": [
                            {
                                "metric": "NEW_ISSUES",
                                "comparator": "GREATER_THAN",
                                "thresholdValue": 10
                            }
                        ]
                    }
                    """)
            .when()
                .post("/api/" + workspaceSlug + "/quality-gates")
            .then()
                .statusCode(201)
                .extract().path("id");

            authenticatedRequest("qgowner")
            .when()
                .post("/api/" + workspaceSlug + "/quality-gates/" + gateId + "/set-default")
            .then()
                .statusCode(200)
                .body("isDefault", is(true));
        }
    }

    // ───────────────────────────────────────────────
    // Delete Quality Gate
    // ───────────────────────────────────────────────
    @Nested
    @DisplayName("DELETE /api/{workspace}/quality-gates/{id}")
    class DeleteQualityGate {

        @Test
        @DisplayName("Delete quality gate — 204")
        void deleteQualityGate_succeeds() {
            int gateId = authenticatedRequest("qgowner")
                .body("""
                    {
                        "name": "Deletable Gate",
                        "conditions": [
                            {
                                "metric": "ISSUES_BY_SEVERITY",
                                "severity": "HIGH",
                                "comparator": "GREATER_THAN",
                                "thresholdValue": 5
                            }
                        ]
                    }
                    """)
            .when()
                .post("/api/" + workspaceSlug + "/quality-gates")
            .then()
                .statusCode(201)
                .extract().path("id");

            authenticatedRequest("qgowner")
            .when()
                .delete("/api/" + workspaceSlug + "/quality-gates/" + gateId)
            .then()
                .statusCode(204);
        }

        @Test
        @DisplayName("Delete non-existent gate — 404")
        void deleteQualityGate_notFound_returns404() {
            authenticatedRequest("qgowner")
            .when()
                .delete("/api/" + workspaceSlug + "/quality-gates/999999")
            .then()
                .statusCode(anyOf(is(404), is(400)));
        }
    }
}
