package org.rostilos.codecrow.webserver;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import static io.restassured.RestAssured.given;
import static org.hamcrest.Matchers.*;

/**
 * Integration tests for WorkspaceController.
 * Tests workspace CRUD, member management, invitations, and deletion scheduling.
 */
class WorkspaceControllerIT extends BaseWebServerIT {

    // ───────────────────────────────────────────────
    // Workspace Creation
    // ───────────────────────────────────────────────
    @Nested
    @DisplayName("POST /api/workspace/create")
    class CreateWorkspace {

        @Test
        @DisplayName("Create workspace with valid data — 201")
        void createWorkspace_validData_returns201() {
            createTestUser("wsowner", "wsowner@example.com", "password123");

            authenticatedRequest("wsowner")
                .body("""
                    {
                        "slug": "my-workspace",
                        "name": "My Workspace",
                        "description": "A test workspace"
                    }
                    """)
            .when()
                .post("/api/workspace/create")
            .then()
                .statusCode(201)
                .body("slug", equalTo("my-workspace"))
                .body("name", equalTo("My Workspace"))
                .body("id", notNullValue());
        }

        @Test
        @DisplayName("Create workspace without description — 201")
        void createWorkspace_noDescription_returns201() {
            createTestUser("wsowner2", "wsowner2@example.com", "password123");

            authenticatedRequest("wsowner2")
                .body("""
                    {
                        "slug": "no-desc-ws",
                        "name": "No Description WS"
                    }
                    """)
            .when()
                .post("/api/workspace/create")
            .then()
                .statusCode(201)
                .body("slug", equalTo("no-desc-ws"));
        }

        @Test
        @DisplayName("Create workspace with invalid slug format — 400")
        void createWorkspace_invalidSlug_returns400() {
            createTestUser("sluguser", "sluguser@example.com", "password123");

            authenticatedRequest("sluguser")
                .body("""
                    {
                        "slug": "Invalid Slug!",
                        "name": "Test"
                    }
                    """)
            .when()
                .post("/api/workspace/create")
            .then()
                .statusCode(400);
        }

        @Test
        @DisplayName("Create workspace with too-short slug — 400")
        void createWorkspace_shortSlug_returns400() {
            createTestUser("shortsluguser", "shortslug@example.com", "password123");

            authenticatedRequest("shortsluguser")
                .body("""
                    {
                        "slug": "ab",
                        "name": "Short Slug WS"
                    }
                    """)
            .when()
                .post("/api/workspace/create")
            .then()
                .statusCode(400);
        }

        @Test
        @DisplayName("Create workspace with missing name — 400")
        void createWorkspace_missingName_returns400() {
            createTestUser("nonameuser", "noname@example.com", "password123");

            authenticatedRequest("nonameuser")
                .body("""
                    {
                        "slug": "valid-slug"
                    }
                    """)
            .when()
                .post("/api/workspace/create")
            .then()
                .statusCode(400);
        }

        @Test
        @DisplayName("Create workspace — unauthenticated — 401")
        void createWorkspace_unauthenticated_returns401() {
            unauthenticatedRequest()
                .body("""
                    {
                        "slug": "unauth-ws",
                        "name": "Unauth WS"
                    }
                    """)
            .when()
                .post("/api/workspace/create")
            .then()
                .statusCode(401);
        }

        @Test
        @DisplayName("Create workspace with duplicate slug — 409/400")
        void createWorkspace_duplicateSlug_returnsError() {
            createTestUser("dupsluguser", "dupslug@example.com", "password123");

            // Create first workspace
            authenticatedRequest("dupsluguser")
                .body("""
                    {
                        "slug": "dup-slug",
                        "name": "First WS"
                    }
                    """)
            .when()
                .post("/api/workspace/create")
            .then()
                .statusCode(201);

            // Try duplicate
            authenticatedRequest("dupsluguser")
                .body("""
                    {
                        "slug": "dup-slug",
                        "name": "Second WS"
                    }
                    """)
            .when()
                .post("/api/workspace/create")
            .then()
                .statusCode(anyOf(is(400), is(409), is(500)));
        }
    }

    // ───────────────────────────────────────────────
    // Workspace Listing
    // ───────────────────────────────────────────────
    @Nested
    @DisplayName("GET /api/workspace/list")
    class ListWorkspaces {

        @Test
        @DisplayName("List workspaces for user with no workspaces — empty")
        void listWorkspaces_noWorkspaces_returnsEmpty() {
            createTestUser("emptylistuser", "emptylist@example.com", "password123");

            authenticatedRequest("emptylistuser")
            .when()
                .get("/api/workspace/list")
            .then()
                .statusCode(200)
                .body("$", hasSize(0));
        }

        @Test
        @DisplayName("List workspaces after creating one")
        void listWorkspaces_afterCreate_returnsWorkspace() {
            createTestUser("listuser", "listuser@example.com", "password123");

            // Create a workspace first
            authenticatedRequest("listuser")
                .body("""
                    {
                        "slug": "listed-ws",
                        "name": "Listed Workspace"
                    }
                    """)
            .when()
                .post("/api/workspace/create")
            .then()
                .statusCode(201);

            // Now list
            authenticatedRequest("listuser")
            .when()
                .get("/api/workspace/list")
            .then()
                .statusCode(200)
                .body("$", hasSize(1))
                .body("[0].slug", equalTo("listed-ws"))
                .body("[0].name", equalTo("Listed Workspace"));
        }

        @Test
        @DisplayName("List workspaces — unauthenticated — 401")
        void listWorkspaces_unauthenticated_returns401() {
            unauthenticatedRequest()
            .when()
                .get("/api/workspace/list")
            .then()
                .statusCode(401);
        }
    }

    // ───────────────────────────────────────────────
    // Workspace Member Management
    // ───────────────────────────────────────────────
    @Nested
    @DisplayName("Member Management")
    class MemberManagement {

        @Test
        @DisplayName("Owner can list members of their workspace")
        void listMembers_owner_succeeds() {
            createTestUser("membowner", "membowner@example.com", "password123");

            // Create workspace
            authenticatedRequest("membowner")
                .body("""
                    {
                        "slug": "member-ws",
                        "name": "Member WS"
                    }
                    """)
            .when()
                .post("/api/workspace/create")
            .then()
                .statusCode(201);

            // List members (owner is auto-added)
            authenticatedRequest("membowner")
            .when()
                .get("/api/workspace/member-ws/members")
            .then()
                .statusCode(200)
                .body("$", hasSize(greaterThanOrEqualTo(1)));
        }

        @Test
        @DisplayName("Owner can get their role in workspace")
        void getUserRole_owner_returnsOwner() {
            createTestUser("roleowner", "roleowner@example.com", "password123");

            authenticatedRequest("roleowner")
                .body("""
                    {
                        "slug": "role-ws",
                        "name": "Role WS"
                    }
                    """)
            .when()
                .post("/api/workspace/create");

            authenticatedRequest("roleowner")
            .when()
                .get("/api/workspace/role-ws/role")
            .then()
                .statusCode(200)
                .body("role", equalTo("OWNER"));
        }

        @Test
        @DisplayName("Invite a user to workspace")
        void inviteUser_validData_succeeds() {
            createTestUser("invowner", "invowner@example.com", "password123");
            createTestUser("invitee", "invitee@example.com", "password123");

            // Create workspace
            authenticatedRequest("invowner")
                .body("""
                    {
                        "slug": "invite-ws",
                        "name": "Invite WS"
                    }
                    """)
            .when()
                .post("/api/workspace/create")
            .then()
                .statusCode(201);

            // Invite user
            authenticatedRequest("invowner")
                .body("""
                    {
                        "username": "invitee",
                        "role": "MEMBER"
                    }
                    """)
            .when()
                .post("/api/workspace/invite-ws/invite")
            .then()
                .statusCode(200);
        }

        @Test
        @DisplayName("Non-member cannot list members — 403")
        void listMembers_nonMember_returns403() {
            createTestUser("ws2owner", "ws2owner@example.com", "password123");
            createTestUser("stranger", "stranger@example.com", "password123");

            authenticatedRequest("ws2owner")
                .body("""
                    {
                        "slug": "private-ws",
                        "name": "Private WS"
                    }
                    """)
            .when()
                .post("/api/workspace/create");

            authenticatedRequest("stranger")
            .when()
                .get("/api/workspace/private-ws/members")
            .then()
                .statusCode(403);
        }

        @Test
        @DisplayName("Invite with invalid role — 400")
        void inviteUser_invalidRole_returns400() {
            createTestUser("badrolown", "badrolown@example.com", "password123");
            createTestUser("badinvitee", "badinvitee@example.com", "password123");

            authenticatedRequest("badrolown")
                .body("""
                    {
                        "slug": "badrole-ws",
                        "name": "Bad Role WS"
                    }
                    """)
            .when()
                .post("/api/workspace/create");

            authenticatedRequest("badrolown")
                .body("""
                    {
                        "username": "badinvitee",
                        "role": "SUPERADMIN"
                    }
                    """)
            .when()
                .post("/api/workspace/badrole-ws/invite")
            .then()
                .statusCode(400);
        }
    }

    // ───────────────────────────────────────────────
    // Workspace Deletion
    // ───────────────────────────────────────────────
    @Nested
    @DisplayName("Workspace Deletion")
    class WorkspaceDeletion {

        @Test
        @DisplayName("Non-owner cannot delete workspace — 403")
        void deleteWorkspace_nonOwner_returns403() {
            createTestUser("delowner", "delowner@example.com", "password123");
            createTestUser("delmember", "delmember@example.com", "password123");

            authenticatedRequest("delowner")
                .body("""
                    {
                        "slug": "del-ws",
                        "name": "Delete WS"
                    }
                    """)
            .when()
                .post("/api/workspace/create");

            // Invite and accept
            authenticatedRequest("delowner")
                .body("""
                    {
                        "username": "delmember",
                        "role": "MEMBER"
                    }
                    """)
            .when()
                .post("/api/workspace/del-ws/invite");

            authenticatedRequest("delmember")
            .when()
                .post("/api/workspace/del-ws/invite/accept");

            // Member tries to delete — should be forbidden
            authenticatedRequest("delmember")
                .body("""
                    {
                        "confirmationSlug": "del-ws",
                        "twoFactorCode": "000000"
                    }
                    """)
            .when()
                .delete("/api/workspace/del-ws")
            .then()
                .statusCode(403);
        }

        @Test
        @DisplayName("Get deletion status — not scheduled")
        void getDeletionStatus_notScheduled_returnsNotScheduled() {
            createTestUser("delstatuser", "delstat@example.com", "password123");

            authenticatedRequest("delstatuser")
                .body("""
                    {
                        "slug": "delstat-ws",
                        "name": "Del Status WS"
                    }
                    """)
            .when()
                .post("/api/workspace/create");

            authenticatedRequest("delstatuser")
            .when()
                .get("/api/workspace/delstat-ws/deletion-status")
            .then()
                .statusCode(200)
                .body("isScheduledForDeletion", is(false));
        }
    }
}
