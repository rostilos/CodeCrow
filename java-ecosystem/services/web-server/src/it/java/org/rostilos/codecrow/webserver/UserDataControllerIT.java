package org.rostilos.codecrow.webserver;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import static io.restassured.RestAssured.given;
import static org.hamcrest.Matchers.*;

/**
 * Integration tests for UserDataController.
 * Tests user info retrieval, update, partial update, existence check, and password change.
 */
class UserDataControllerIT extends BaseWebServerIT {

    @Nested
    @DisplayName("GET /api/user_info/current")
    class GetCurrentUser {

        @Test
        @DisplayName("Authenticated user retrieves their own info")
        void getCurrentUser_authenticated_returnsUserInfo() {
            createTestUser("currentuser", "current@example.com", "password123");

            authenticatedRequest("currentuser")
            .when()
                .get("/api/user_info/current")
            .then()
                .statusCode(200)
                .body("username", equalTo("currentuser"))
                .body("email", equalTo("current@example.com"))
                .body("id", notNullValue());
        }

        @Test
        @DisplayName("Unauthenticated request — 401")
        void getCurrentUser_unauthenticated_returns401() {
            unauthenticatedRequest()
            .when()
                .get("/api/user_info/current")
            .then()
                .statusCode(401);
        }

        @Test
        @DisplayName("Admin user has correct role context")
        void getCurrentUser_admin_returnsAdminInfo() {
            createAdminUser("adminuser", "admin@example.com", "password123");

            authenticatedRequest("adminuser")
            .when()
                .get("/api/user_info/current")
            .then()
                .statusCode(200)
                .body("username", equalTo("adminuser"))
                .body("email", equalTo("admin@example.com"));
        }
    }

    @Nested
    @DisplayName("PUT /api/user_info/update")
    class UpdateUserData {

        @Test
        @DisplayName("Full update returns new JWT and updated data")
        void updateUser_validData_returnsNewJwt() {
            createTestUser("updateuser", "update@example.com", "password123");

            authenticatedRequest("updateuser")
                .body("""
                    {
                        "username": "updatedname",
                        "email": "newemail@example.com",
                        "company": "NewCompany"
                    }
                    """)
            .when()
                .put("/api/user_info/update")
            .then()
                .statusCode(200)
                .body("accessToken", notNullValue())
                .body("username", equalTo("updatedname"))
                .body("email", equalTo("newemail@example.com"))
                .body("company", equalTo("NewCompany"));
        }

        @Test
        @DisplayName("Update with invalid email — 400")
        void updateUser_invalidEmail_returns400() {
            createTestUser("emailuser", "emailtest@example.com", "password123");

            authenticatedRequest("emailuser")
                .body("""
                    {
                        "username": "emailuser",
                        "email": "not-an-email",
                        "company": "Test"
                    }
                    """)
            .when()
                .put("/api/user_info/update")
            .then()
                .statusCode(400);
        }

        @Test
        @DisplayName("Update with short username — 400")
        void updateUser_shortUsername_returns400() {
            createTestUser("shortuser", "shortuser@example.com", "password123");

            authenticatedRequest("shortuser")
                .body("""
                    {
                        "username": "ab",
                        "email": "shortuser@example.com"
                    }
                    """)
            .when()
                .put("/api/user_info/update")
            .then()
                .statusCode(400);
        }

        @Test
        @DisplayName("Unauthenticated update — 401")
        void updateUser_unauthenticated_returns401() {
            unauthenticatedRequest()
                .body("""
                    {
                        "username": "hacker",
                        "email": "hack@example.com"
                    }
                    """)
            .when()
                .put("/api/user_info/update")
            .then()
                .statusCode(401);
        }
    }

    @Nested
    @DisplayName("PATCH /api/user_info/update-partial")
    class PartialUpdateUserData {

        @Test
        @DisplayName("Partial update with only company field")
        void partialUpdate_companyOnly_succeeds() {
            createTestUser("partialuser", "partial@example.com", "password123");

            authenticatedRequest("partialuser")
                .body("""
                    {
                        "company": "PartialCo"
                    }
                    """)
            .when()
                .patch("/api/user_info/update-partial")
            .then()
                .statusCode(200)
                .body("message", containsString("successfully"));
        }

        @Test
        @DisplayName("Partial update with empty body returns 400")
        void partialUpdate_emptyBody_returns400() {
            createTestUser("emptyuser", "emptyuser@example.com", "password123");

            authenticatedRequest("emptyuser")
                .body("{}")
            .when()
                .patch("/api/user_info/update-partial")
            .then()
                .statusCode(400);
        }
    }

    @Nested
    @DisplayName("GET /api/user_info/check-exists/{userId}")
    class CheckUserExists {

        @Test
        @DisplayName("Check existing user returns true")
        void checkExists_existingUser_returnsTrue() {
            Long userId = createTestUser("existsuser", "exists@example.com", "password123");

            authenticatedRequest("existsuser")
            .when()
                .get("/api/user_info/check-exists/" + userId)
            .then()
                .statusCode(200)
                .body("message", containsString("true"));
        }

        @Test
        @DisplayName("Check non-existing user returns false")
        void checkExists_nonExistingUser_returnsFalse() {
            createTestUser("checker", "checker@example.com", "password123");

            authenticatedRequest("checker")
            .when()
                .get("/api/user_info/check-exists/999999")
            .then()
                .statusCode(200)
                .body("message", containsString("false"));
        }

        @Test
        @DisplayName("Unauthenticated check — 401")
        void checkExists_unauthenticated_returns401() {
            unauthenticatedRequest()
            .when()
                .get("/api/user_info/check-exists/1")
            .then()
                .statusCode(401);
        }
    }

    @Nested
    @DisplayName("PUT /api/user_info/change-password")
    class ChangePassword {

        @Test
        @DisplayName("Change password with correct current password succeeds")
        void changePassword_validData_succeeds() {
            createTestUser("pwduser", "pwduser@example.com", "oldPassword1");

            authenticatedRequest("pwduser")
                .body("""
                    {
                        "currentPassword": "oldPassword1",
                        "newPassword": "newPassword2",
                        "confirmPassword": "newPassword2"
                    }
                    """)
            .when()
                .put("/api/user_info/change-password")
            .then()
                .statusCode(200)
                .body("message", containsString("successfully"));
        }

        @Test
        @DisplayName("Mismatched new password and confirmation — 400/error")
        void changePassword_mismatch_returns400() {
            createTestUser("mismatchuser", "mismatch@example.com", "password123");

            authenticatedRequest("mismatchuser")
                .body("""
                    {
                        "currentPassword": "password123",
                        "newPassword": "newPass1",
                        "confirmPassword": "differentPass2"
                    }
                    """)
            .when()
                .put("/api/user_info/change-password")
            .then()
                .statusCode(anyOf(is(400), is(401)));
        }

        @Test
        @DisplayName("Wrong current password — error")
        void changePassword_wrongCurrentPassword_returnsError() {
            createTestUser("wrongpwd", "wrongpwd@example.com", "password123");

            authenticatedRequest("wrongpwd")
                .body("""
                    {
                        "currentPassword": "wrongPassword",
                        "newPassword": "newPassword2",
                        "confirmPassword": "newPassword2"
                    }
                    """)
            .when()
                .put("/api/user_info/change-password")
            .then()
                .statusCode(anyOf(is(400), is(401)));
        }

        @Test
        @DisplayName("Short new password — 400")
        void changePassword_shortNewPassword_returns400() {
            createTestUser("shortpwduser", "shortpwd@example.com", "password123");

            authenticatedRequest("shortpwduser")
                .body("""
                    {
                        "currentPassword": "password123",
                        "newPassword": "ab",
                        "confirmPassword": "ab"
                    }
                    """)
            .when()
                .put("/api/user_info/change-password")
            .then()
                .statusCode(400);
        }

        @Test
        @DisplayName("Unauthenticated change-password — 401")
        void changePassword_unauthenticated_returns401() {
            unauthenticatedRequest()
                .body("""
                    {
                        "currentPassword": "a",
                        "newPassword": "b",
                        "confirmPassword": "b"
                    }
                    """)
            .when()
                .put("/api/user_info/change-password")
            .then()
                .statusCode(401);
        }
    }
}
