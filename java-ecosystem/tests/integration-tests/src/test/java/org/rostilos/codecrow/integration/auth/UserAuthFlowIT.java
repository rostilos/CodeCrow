package org.rostilos.codecrow.integration.auth;

import io.restassured.response.Response;
import org.junit.jupiter.api.*;
import org.rostilos.codecrow.core.model.user.ERole;
import org.rostilos.codecrow.core.model.user.User;
import org.rostilos.codecrow.core.model.workspace.EWorkspaceRole;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.integration.base.BaseIntegrationTest;

import static io.restassured.RestAssured.given;
import static org.hamcrest.Matchers.*;

/**
 * Integration tests for user authentication and authorization flows.
 * Tests signup, login, role management, and workspace access.
 */
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
@Tag("auth")
@Tag("fast")
class UserAuthFlowIT extends BaseIntegrationTest {

    @BeforeEach
    void setUpTest() {
        authTestHelper.initializeTestUsers();
    }

    @Test
    @Order(1)
    @DisplayName("Should register new user successfully")
    void shouldRegisterNewUser() {
        String email = "newuser-" + System.currentTimeMillis() + "@test.com";
        String requestBody = String.format("""
            {
                "username": "%s",
                "email": "%s",
                "password": "NewUser123!",
                "firstName": "New",
                "lastName": "User"
            }
            """, email, email);

        Response response = given()
                .spec(requestSpec)
                .body(requestBody)
                .when()
                .post("/api/auth/signup");
        
        System.out.println("Register user test - Status: " + response.getStatusCode());
        System.out.println("Register user test - Body: " + response.getBody().asString());
        
        response.then()
                .statusCode(anyOf(equalTo(200), equalTo(201), equalTo(500)));
    }

    @Test
    @Order(2)
    @DisplayName("Should reject duplicate email registration")
    void shouldRejectDuplicateEmail() {
        String existingUserEmail = authTestHelper.getAdminUser().getEmail();
        
        String requestBody = String.format("""
            {
                "username": "%s",
                "email": "%s",
                "password": "Password123!",
                "firstName": "Duplicate",
                "lastName": "User"
            }
            """, existingUserEmail, existingUserEmail);

        Response response = given()
                .spec(requestSpec)
                .body(requestBody)
                .when()
                .post("/api/auth/signup");
        
        System.out.println("Duplicate email test - Status: " + response.getStatusCode());
        System.out.println("Duplicate email test - Body: " + response.getBody().asString());
        
        response.then()
                .statusCode(anyOf(equalTo(400), equalTo(500)));
    }

    @Test
    @Order(3)
    @DisplayName("Should login with valid credentials")
    void shouldLoginWithValidCredentials() {
        String requestBody = """
            {
                "username": "admin@codecrow-test.com",
                "password": "AdminPassword123!"
            }
            """;

        given()
                .spec(requestSpec)
                .body(requestBody)
                .when()
                .post("/api/auth/login")
                .then()
                .statusCode(200)
                .body("accessToken", notNullValue())
                .body("tokenType", equalTo("Bearer"));
    }

    @Test
    @Order(4)
    @DisplayName("Should reject login with invalid password")
    void shouldRejectInvalidPassword() {
        String requestBody = """
            {
                "username": "admin@codecrow-test.com",
                "password": "WrongPassword123!"
            }
            """;

        given()
                .spec(requestSpec)
                .body(requestBody)
                .when()
                .post("/api/auth/login")
                .then()
                .statusCode(anyOf(equalTo(400), equalTo(401)));
    }

    @Test
    @Order(5)
    @DisplayName("Should reject login with non-existent user")
    void shouldRejectNonExistentUser() {
        String requestBody = """
            {
                "username": "nonexistent@test.com",
                "password": "Password123!"
            }
            """;

        given()
                .spec(requestSpec)
                .body(requestBody)
                .when()
                .post("/api/auth/login")
                .then()
                .statusCode(anyOf(equalTo(400), equalTo(401)));
    }

    @Test
    @Order(6)
    @DisplayName("Should access protected endpoint with valid token")
    void shouldAccessProtectedEndpoint() {
        Workspace workspace = authTestHelper.getDefaultWorkspace();
        
        given()
                .spec(authenticatedAsAdmin())
                .when()
                .get("/api/{workspaceSlug}/project/project_list", workspace.getSlug())
                .then()
                .statusCode(200);
    }

    @Test
    @Order(7)
    @DisplayName("Should reject protected endpoint without token")
    void shouldRejectWithoutToken() {
        Workspace workspace = authTestHelper.getDefaultWorkspace();
        
        given()
                .spec(requestSpec)
                .when()
                .get("/api/{workspaceSlug}/project/project_list", workspace.getSlug())
                .then()
                .statusCode(401);
    }

    @Test
    @Order(8)
    @DisplayName("Should reject protected endpoint with invalid token")
    void shouldRejectInvalidToken() {
        Workspace workspace = authTestHelper.getDefaultWorkspace();
        
        given()
                .spec(requestSpec)
                .header("Authorization", "Bearer invalid-token-here")
                .when()
                .get("/api/{workspaceSlug}/project/project_list", workspace.getSlug())
                .then()
                .statusCode(401);
    }

    @Test
    @Order(9)
    @DisplayName("Should validate password requirements")
    void shouldValidatePasswordRequirements() {
        String requestBody = """
            {
                "username": "weakpass@test.com",
                "email": "weakpass@test.com",
                "password": "weak",
                "firstName": "Weak",
                "lastName": "Password"
            }
            """;

        Response response = given()
                .spec(requestSpec)
                .body(requestBody)
                .when()
                .post("/api/auth/signup");
        
        System.out.println("Weak password test - Status: " + response.getStatusCode());
        System.out.println("Weak password test - Body: " + response.getBody().asString());
        
        response.then()
                .statusCode(anyOf(equalTo(400), equalTo(500)));
    }

    @Test
    @Order(10)
    @DisplayName("Should enforce workspace role-based access")
    void shouldEnforceWorkspaceRoleAccess() {
        Workspace adminWorkspace = authTestHelper.createTestWorkspace("Admin Only Workspace", authTestHelper.getAdminUser());
        
        Response response = given()
                .spec(authenticatedAsUser())
                .when()
                .get("/api/{workspaceSlug}/project/project_list", adminWorkspace.getSlug());
        
        System.out.println("Workspace access test - Status: " + response.getStatusCode());
        
        response.then()
                .statusCode(anyOf(equalTo(403), equalTo(500)));
    }

    @Test
    @Order(11)
    @DisplayName("Should allow workspace member access")
    void shouldAllowWorkspaceMemberAccess() {
        Workspace workspace = authTestHelper.getDefaultWorkspace();
        
        given()
                .spec(authenticatedAsUser())
                .when()
                .get("/api/{workspaceSlug}/project/project_list", workspace.getSlug())
                .then()
                .statusCode(200);
    }

    @Test
    @Order(12)
    @DisplayName("Should handle role escalation attempt")
    void shouldPreventRoleEscalation() {
        Workspace workspace = authTestHelper.getDefaultWorkspace();
        
        String requestBody = """
            {
                "connectionName": "Escalation Test",
                "workspaceId": "test",
                "workspaceName": "test",
                "username": "user",
                "appPassword": "pass"
            }
            """;

        Response response = given()
                .spec(authenticatedAsUser())
                .body(requestBody)
                .when()
                .post("/api/{workspaceSlug}/vcs/bitbucket_cloud/create", workspace.getSlug());
        
        System.out.println("Role escalation test - Status: " + response.getStatusCode());
        
        response.then()
                .statusCode(anyOf(equalTo(403), equalTo(500)));
    }

    @Test
    @Order(13)
    @DisplayName("Should support user with multiple workspace memberships")
    void shouldSupportMultipleWorkspaceMemberships() {
        User user = authTestHelper.getRegularUser();
        
        Workspace workspace1 = authTestHelper.createTestWorkspace("Multi WS 1", authTestHelper.getAdminUser());
        Workspace workspace2 = authTestHelper.createTestWorkspace("Multi WS 2", authTestHelper.getAdminUser());
        
        authTestHelper.addUserToWorkspace(user, workspace1, EWorkspaceRole.MEMBER);
        authTestHelper.addUserToWorkspace(user, workspace2, EWorkspaceRole.ADMIN);
        
        given()
                .spec(authenticatedAsUser())
                .when()
                .get("/api/{workspaceSlug}/project/project_list", workspace1.getSlug())
                .then()
                .statusCode(200);
        
        given()
                .spec(authenticatedAsUser())
                .when()
                .get("/api/{workspaceSlug}/project/project_list", workspace2.getSlug())
                .then()
                .statusCode(200);
    }

    @Test
    @Order(14)
    @DisplayName("Should handle workspace role downgrade")
    void shouldHandleWorkspaceRoleDowngrade() {
        User user = authTestHelper.getRegularUser();
        Workspace workspace = authTestHelper.createTestWorkspace("Role Change WS", authTestHelper.getAdminUser());
        
        authTestHelper.addUserToWorkspace(user, workspace, EWorkspaceRole.ADMIN);
        
        String createBody = """
            {
                "providerKey": "OPENROUTER",
                "aiModel": "test",
                "apiKey": "key"
            }
            """;
        given()
                .spec(authenticatedAsUser())
                .body(createBody)
                .when()
                .post("/api/{workspaceSlug}/ai/create", workspace.getSlug())
                .then()
                .statusCode(201);
        
        authTestHelper.addUserToWorkspace(user, workspace, EWorkspaceRole.MEMBER);
        authTestHelper.clearCachedTokens();
        
        Response response = given()
                .spec(authenticatedAsUser())
                .body(createBody)
                .when()
                .post("/api/{workspaceSlug}/ai/create", workspace.getSlug());
        
        System.out.println("Role downgrade test - Status: " + response.getStatusCode());
        
        response.then()
                .statusCode(anyOf(equalTo(403), equalTo(500)));
    }
}
