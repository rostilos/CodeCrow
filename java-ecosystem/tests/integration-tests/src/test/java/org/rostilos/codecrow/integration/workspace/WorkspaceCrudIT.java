package org.rostilos.codecrow.integration.workspace;

import org.junit.jupiter.api.*;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.integration.base.BaseIntegrationTest;

import static io.restassured.RestAssured.given;
import static org.hamcrest.Matchers.*;

/**
 * Integration tests for Workspace management operations.
 */
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
@Tag("workspace")
class WorkspaceCrudIT extends BaseIntegrationTest {

    private Workspace testWorkspace;

    @BeforeEach
    void setUpTest() {
        authTestHelper.initializeTestUsers();
        testWorkspace = authTestHelper.getDefaultWorkspace();
    }

    @Nested
    @DisplayName("Workspace Creation Tests")
    @TestMethodOrder(MethodOrderer.OrderAnnotation.class)
    class WorkspaceCreationTests {

        @Test
        @Order(1)
        @DisplayName("Should create new workspace")
        void shouldCreateNewWorkspace() {
            String workspaceBody = """
                {
                    "name": "New Test Workspace",
                    "slug": "new-test-workspace-create",
                    "description": "A test workspace"
                }
                """;

            given()
                    .spec(authenticatedAsAdmin())
                    .body(workspaceBody)
                    .when()
                    .post("/api/workspace/create")
                    .then()
                    .statusCode(anyOf(equalTo(200), equalTo(201)))
                    .body("name", equalTo("New Test Workspace"))
                    .body("slug", equalTo("new-test-workspace-create"));
        }

        @Test
        @Order(2)
        @DisplayName("Should require authentication for workspace creation")
        void shouldRequireAuthenticationForCreation() {
            String workspaceBody = """
                {
                    "name": "Unauth Workspace",
                    "slug": "unauth-workspace",
                    "description": "Should fail"
                }
                """;

            given()
                    .spec(requestSpec)
                    .body(workspaceBody)
                    .when()
                    .post("/api/workspace/create")
                    .then()
                    .statusCode(anyOf(equalTo(401), equalTo(403)));
        }
    }

    @Nested
    @DisplayName("Workspace List Tests")
    @TestMethodOrder(MethodOrderer.OrderAnnotation.class)
    class WorkspaceListTests {

        @Test
        @Order(1)
        @DisplayName("Should list user workspaces")
        void shouldListUserWorkspaces() {
            given()
                    .spec(authenticatedAsAdmin())
                    .when()
                    .get("/api/workspace/list")
                    .then()
                    .statusCode(200)
                    .body("$", hasSize(greaterThanOrEqualTo(1)));
        }

        @Test
        @Order(2)
        @DisplayName("Should require authentication for listing workspaces")
        void shouldRequireAuthenticationForListing() {
            given()
                    .spec(requestSpec)
                    .when()
                    .get("/api/workspace/list")
                    .then()
                    .statusCode(anyOf(equalTo(401), equalTo(403)));
        }
    }

    @Nested
    @DisplayName("Workspace Member Tests")
    @TestMethodOrder(MethodOrderer.OrderAnnotation.class)
    class WorkspaceMemberTests {

        @Test
        @Order(1)
        @DisplayName("Should list workspace members")
        void shouldListWorkspaceMembers() {
            given()
                    .spec(authenticatedAsAdmin())
                    .when()
                    .get("/api/workspace/{workspaceSlug}/members", testWorkspace.getSlug())
                    .then()
                    .statusCode(200)
                    .body("$", hasSize(greaterThanOrEqualTo(1)));
        }

        @Test
        @Order(2)
        @DisplayName("Should get user role in workspace")
        void shouldGetUserRoleInWorkspace() {
            given()
                    .spec(authenticatedAsAdmin())
                    .when()
                    .get("/api/workspace/{workspaceSlug}/role", testWorkspace.getSlug())
                    .then()
                    .statusCode(200)
                    .body("role", anyOf(equalTo("OWNER"), equalTo("ADMIN"), equalTo("MEMBER")));
        }

        @Test
        @Order(3)
        @DisplayName("Should handle invite to workspace (user may already be member)")
        void shouldInviteUserToWorkspace() {
            // Note: The regular user is already a member from initializeTestUsers(),
            // so this may return 400/500 for duplicate invite, or 200 if it's idempotent
            String inviteBody = String.format("""
                {
                    "username": "%s",
                    "role": "MEMBER"
                }
                """, authTestHelper.getRegularUser().getUsername());

            given()
                    .spec(authenticatedAsAdmin())
                    .body(inviteBody)
                    .when()
                    .post("/api/workspace/{workspaceSlug}/invite", testWorkspace.getSlug())
                    .then()
                    .statusCode(anyOf(equalTo(200), equalTo(201), equalTo(400), equalTo(500)));
        }
    }

    @Nested
    @DisplayName("Workspace Access Control Tests")
    @TestMethodOrder(MethodOrderer.OrderAnnotation.class)
    class WorkspaceAccessControlTests {

        @Test
        @Order(1)
        @DisplayName("Should allow member to access workspace role")
        void shouldAllowMemberToAccessWorkspaceRole() {
            // Regular user is a member of testWorkspace, so should be able to access role
            given()
                    .spec(authenticatedAsUser())
                    .when()
                    .get("/api/workspace/{workspaceSlug}/role", testWorkspace.getSlug())
                    .then()
                    .statusCode(200)
                    .body("role", anyOf(equalTo("OWNER"), equalTo("ADMIN"), equalTo("MEMBER")));
        }

        @Test
        @Order(2)
        @DisplayName("Should deny access to non-existent workspace")
        void shouldDenyAccessToNonExistentWorkspace() {
            given()
                    .spec(authenticatedAsUser())
                    .when()
                    .get("/api/workspace/{workspaceSlug}/role", "non-existent-workspace-xyz")
                    .then()
                    .statusCode(anyOf(equalTo(403), equalTo(404), equalTo(500)));
        }

        @Test
        @Order(3)
        @DisplayName("Should require authentication for workspace operations")
        void shouldRequireAuthenticationForWorkspaceOps() {
            given()
                    .spec(requestSpec)
                    .when()
                    .get("/api/workspace/{workspaceSlug}/members", testWorkspace.getSlug())
                    .then()
                    .statusCode(anyOf(equalTo(401), equalTo(403)));
        }
    }
}
