package org.rostilos.codecrow.webserver;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import static io.restassured.RestAssured.given;
import static org.hamcrest.Matchers.*;

/**
 * Integration tests for ProjectController.
 * Projects live within a workspace and inherit workspace-level authorization.
 */
class ProjectControllerIT extends BaseWebServerIT {

    private String workspaceSlug;

    @BeforeEach
    void setupWorkspace() {
        createTestUser("projowner", "projowner@example.com", "password123");

        workspaceSlug = "proj-test-ws";
        authenticatedRequest("projowner")
            .body("""
                {
                    "slug": "%s",
                    "name": "Project Test Workspace"
                }
                """.formatted(workspaceSlug))
        .when()
            .post("/api/workspace/create")
        .then()
            .statusCode(201);
    }

    // ───────────────────────────────────────────────
    // List Projects
    // ───────────────────────────────────────────────
    @Nested
    @DisplayName("GET /api/{workspace}/project/project_list")
    class ListProjects {

        @Test
        @DisplayName("List projects — empty workspace — returns empty list")
        void listProjects_empty_returnsEmptyList() {
            authenticatedRequest("projowner")
            .when()
                .get("/api/" + workspaceSlug + "/project/project_list")
            .then()
                .statusCode(200)
                .body("$", hasSize(0));
        }

        @Test
        @DisplayName("List projects — unauthenticated — 401")
        void listProjects_unauthenticated_returns401() {
            unauthenticatedRequest()
            .when()
                .get("/api/" + workspaceSlug + "/project/project_list")
            .then()
                .statusCode(401);
        }

        @Test
        @DisplayName("List projects — non-member — 403")
        void listProjects_nonMember_returns403() {
            createTestUser("projstranger", "projstranger@example.com", "password123");

            authenticatedRequest("projstranger")
            .when()
                .get("/api/" + workspaceSlug + "/project/project_list")
            .then()
                .statusCode(403);
        }
    }

    // ───────────────────────────────────────────────
    // Paginated Projects
    // ───────────────────────────────────────────────
    @Nested
    @DisplayName("GET /api/{workspace}/project/projects")
    class PaginatedProjects {

        @Test
        @DisplayName("Paginated projects — with default params")
        void paginatedProjects_defaults_returnsPage() {
            authenticatedRequest("projowner")
            .when()
                .get("/api/" + workspaceSlug + "/project/projects")
            .then()
                .statusCode(200);
        }

        @Test
        @DisplayName("Paginated projects with explicit page and size")
        void paginatedProjects_customParams() {
            authenticatedRequest("projowner")
                .queryParam("page", 0)
                .queryParam("size", 5)
            .when()
                .get("/api/" + workspaceSlug + "/project/projects")
            .then()
                .statusCode(200);
        }
    }

    // ───────────────────────────────────────────────
    // Create Project
    // ───────────────────────────────────────────────
    @Nested
    @DisplayName("POST /api/{workspace}/project/create")
    class CreateProject {

        @Test
        @DisplayName("Create project with minimal fields — succeeds")
        void createProject_minimal_succeeds() {
            authenticatedRequest("projowner")
                .body("""
                    {
                        "name": "Test Project",
                        "namespace": "test-project",
                        "description": "A test project",
                        "creationMode": "MANUAL",
                        "mainBranch": "main"
                    }
                    """)
            .when()
                .post("/api/" + workspaceSlug + "/project/create")
            .then()
                .statusCode(anyOf(is(200), is(201)))
                .body("name", equalTo("Test Project"))
                .body("namespace", equalTo("test-project"));
        }

        @Test
        @DisplayName("Create project without name — 400")
        void createProject_missingName_returns400() {
            authenticatedRequest("projowner")
                .body("""
                    {
                        "namespace": "no-name-proj",
                        "creationMode": "MANUAL"
                    }
                    """)
            .when()
                .post("/api/" + workspaceSlug + "/project/create")
            .then()
                .statusCode(400);
        }

        @Test
        @DisplayName("Create project without namespace — 400")
        void createProject_missingNamespace_returns400() {
            authenticatedRequest("projowner")
                .body("""
                    {
                        "name": "No Namespace",
                        "creationMode": "MANUAL"
                    }
                    """)
            .when()
                .post("/api/" + workspaceSlug + "/project/create")
            .then()
                .statusCode(400);
        }

        @Test
        @DisplayName("Create project — unauthenticated — 401")
        void createProject_unauthenticated_returns401() {
            unauthenticatedRequest()
                .body("""
                    {
                        "name": "Hack Project",
                        "namespace": "hack-proj",
                        "creationMode": "MANUAL"
                    }
                    """)
            .when()
                .post("/api/" + workspaceSlug + "/project/create")
            .then()
                .statusCode(401);
        }

        @Test
        @DisplayName("Create project — non-member — 403")
        void createProject_nonMember_returns403() {
            createTestUser("projintruder", "projintruder@example.com", "password123");

            authenticatedRequest("projintruder")
                .body("""
                    {
                        "name": "Intruder Project",
                        "namespace": "intruder-proj",
                        "creationMode": "MANUAL"
                    }
                    """)
            .when()
                .post("/api/" + workspaceSlug + "/project/create")
            .then()
                .statusCode(403);
        }

        @Test
        @DisplayName("Create duplicate project namespace — error")
        void createProject_duplicateNamespace_returnsError() {
            authenticatedRequest("projowner")
                .body("""
                    {
                        "name": "First Project",
                        "namespace": "dup-ns",
                        "creationMode": "MANUAL",
                        "mainBranch": "main"
                    }
                    """)
            .when()
                .post("/api/" + workspaceSlug + "/project/create")
            .then()
                .statusCode(anyOf(is(200), is(201)));

            // Duplicate namespace
            authenticatedRequest("projowner")
                .body("""
                    {
                        "name": "Second Project",
                        "namespace": "dup-ns",
                        "creationMode": "MANUAL",
                        "mainBranch": "main"
                    }
                    """)
            .when()
                .post("/api/" + workspaceSlug + "/project/create")
            .then()
                .statusCode(anyOf(is(400), is(409), is(500)));
        }
    }

    // ───────────────────────────────────────────────
    // Project CRUD operations
    // ───────────────────────────────────────────────
    @Nested
    @DisplayName("Project CRUD operations")
    class ProjectCrud {

        private String projectNs;

        @BeforeEach
        void createProject() {
            projectNs = "crud-project";
            authenticatedRequest("projowner")
                .body("""
                    {
                        "name": "CRUD Project",
                        "namespace": "%s",
                        "description": "Project for CRUD tests",
                        "creationMode": "MANUAL",
                        "mainBranch": "main"
                    }
                    """.formatted(projectNs))
            .when()
                .post("/api/" + workspaceSlug + "/project/create")
            .then()
                .statusCode(anyOf(is(200), is(201)));
        }

        @Test
        @DisplayName("Update project name via PATCH")
        void updateProject_changeName_succeeds() {
            authenticatedRequest("projowner")
                .body("""
                    {
                        "name": "Updated CRUD Project"
                    }
                    """)
            .when()
                .patch("/api/" + workspaceSlug + "/project/" + projectNs)
            .then()
                .statusCode(200)
                .body("name", equalTo("Updated CRUD Project"));
        }

        @Test
        @DisplayName("List projects after creation — contains created project")
        void listProjects_afterCreate_containsProject() {
            authenticatedRequest("projowner")
            .when()
                .get("/api/" + workspaceSlug + "/project/project_list")
            .then()
                .statusCode(200)
                .body("$", hasSize(greaterThanOrEqualTo(1)));
        }

        @Test
        @DisplayName("Generate project token")
        void generateProjectToken_succeeds() {
            authenticatedRequest("projowner")
                .body("""
                    {
                        "name": "ci-token"
                    }
                    """)
            .when()
                .post("/api/" + workspaceSlug + "/project/" + projectNs + "/token/generate")
            .then()
                .statusCode(anyOf(is(200), is(201)))
                .body("token", notNullValue());
        }

        @Test
        @DisplayName("List project tokens")
        void listProjectTokens_succeeds() {
            // Generate a token first
            authenticatedRequest("projowner")
                .body("""
                    {
                        "name": "list-token"
                    }
                    """)
            .when()
                .post("/api/" + workspaceSlug + "/project/" + projectNs + "/token/generate");

            authenticatedRequest("projowner")
            .when()
                .get("/api/" + workspaceSlug + "/project/" + projectNs + "/token")
            .then()
                .statusCode(200)
                .body("$", hasSize(greaterThanOrEqualTo(1)));
        }
    }

    // ───────────────────────────────────────────────
    // Project Configuration
    // ───────────────────────────────────────────────
    @Nested
    @DisplayName("Project Configuration Endpoints")
    class ProjectConfiguration {

        private String projectNs;

        @BeforeEach
        void createProject() {
            projectNs = "config-project";
            authenticatedRequest("projowner")
                .body("""
                    {
                        "name": "Config Project",
                        "namespace": "%s",
                        "creationMode": "MANUAL",
                        "mainBranch": "main"
                    }
                    """.formatted(projectNs))
            .when()
                .post("/api/" + workspaceSlug + "/project/create")
            .then()
                .statusCode(anyOf(is(200), is(201)));
        }

        @Test
        @DisplayName("Get comment commands config")
        void getCommentCommandsConfig_succeeds() {
            authenticatedRequest("projowner")
            .when()
                .get("/api/" + workspaceSlug + "/project/" + projectNs + "/comment-commands-config")
            .then()
                .statusCode(200);
        }

        @Test
        @DisplayName("Get project rules")
        void getProjectRules_succeeds() {
            authenticatedRequest("projowner")
            .when()
                .get("/api/" + workspaceSlug + "/project/" + projectNs + "/project-rules")
            .then()
                .statusCode(200);
        }

        @Test
        @DisplayName("Get RAG status for project")
        void getRagStatus_succeeds() {
            authenticatedRequest("projowner")
            .when()
                .get("/api/" + workspaceSlug + "/project/" + projectNs + "/rag/status")
            .then()
                .statusCode(200);
        }

        @Test
        @DisplayName("Update analysis settings")
        void updateAnalysisSettings_succeeds() {
            authenticatedRequest("projowner")
                .body("""
                    {
                        "analysisEnabled": true
                    }
                    """)
            .when()
                .put("/api/" + workspaceSlug + "/project/" + projectNs + "/analysis-settings")
            .then()
                .statusCode(anyOf(is(200), is(204)));
        }

        @Test
        @DisplayName("Update project quality gate")
        void updateProjectQualityGate_succeeds() {
            // First, get the default quality gate ID
            int gateId = authenticatedRequest("projowner")
            .when()
                .get("/api/" + workspaceSlug + "/quality-gates/default")
            .then()
                .statusCode(200)
                .extract().path("id");

            authenticatedRequest("projowner")
                .body("""
                    {
                        "qualityGateId": %d
                    }
                    """.formatted(gateId))
            .when()
                .put("/api/" + workspaceSlug + "/project/" + projectNs + "/quality-gate")
            .then()
                .statusCode(anyOf(is(200), is(204)));
        }
    }
}
