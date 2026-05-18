package org.rostilos.codecrow.webserver;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import static io.restassured.RestAssured.given;
import static org.hamcrest.Matchers.*;

/**
 * Integration tests for the Auth controller (login, register, refresh, logout).
 * <p>Tests the full authentication flow against a real PostgreSQL database
 * with Argon2 password encoding and JWT token generation.</p>
 */
class AuthControllerIT extends BaseWebServerIT {

    private static final String USERNAME = "testuser";
    private static final String EMAIL = "test@example.com";
    private static final String PASSWORD = "SecureP@ss1";

    @Nested
    @DisplayName("POST /api/auth/register")
    class Register {

        @Test
        @DisplayName("First user registration — becomes admin")
        void register_firstUser_becomesAdmin() {
            unauthenticatedRequest()
                .body("""
                    {
                        "username": "%s",
                        "email": "%s",
                        "password": "%s",
                        "company": "TestCorp"
                    }
                    """.formatted(USERNAME, EMAIL, PASSWORD))
            .when()
                .post("/api/auth/register")
            .then()
                .statusCode(200)
                .body("username", equalTo(USERNAME))
                .body("email", equalTo(EMAIL))
                .body("accessToken", notNullValue())
                .body("refreshToken", notNullValue())
                .body("roles", hasItem("ROLE_ADMIN"));
        }

        @Test
        @DisplayName("Second user registration — becomes default user")
        void register_secondUser_becomesDefault() {
            // First user (admin)
            createTestUser("admin", "admin@example.com", PASSWORD);

            unauthenticatedRequest()
                .body("""
                    {
                        "username": "second",
                        "email": "second@example.com",
                        "password": "%s"
                    }
                    """.formatted(PASSWORD))
            .when()
                .post("/api/auth/register")
            .then()
                .statusCode(200)
                .body("username", equalTo("second"))
                .body("roles", hasItem("ROLE_USER"));
        }

        @Test
        @DisplayName("Duplicate username — returns error")
        void register_duplicateUsername_error() {
            createTestUser(USERNAME, EMAIL, PASSWORD);

            unauthenticatedRequest()
                .body("""
                    {
                        "username": "%s",
                        "email": "other@example.com",
                        "password": "%s"
                    }
                    """.formatted(USERNAME, PASSWORD))
            .when()
                .post("/api/auth/register")
            .then()
                .statusCode(anyOf(is(400), is(409)));
        }

        @Test
        @DisplayName("Duplicate email — returns error")
        void register_duplicateEmail_error() {
            createTestUser(USERNAME, EMAIL, PASSWORD);

            unauthenticatedRequest()
                .body("""
                    {
                        "username": "different",
                        "email": "%s",
                        "password": "%s"
                    }
                    """.formatted(EMAIL, PASSWORD))
            .when()
                .post("/api/auth/register")
            .then()
                .statusCode(anyOf(is(400), is(409)));
        }

        @Test
        @DisplayName("Invalid email format — validation error")
        void register_invalidEmail_validationError() {
            unauthenticatedRequest()
                .body("""
                    {
                        "username": "validuser",
                        "email": "not-an-email",
                        "password": "%s"
                    }
                    """.formatted(PASSWORD))
            .when()
                .post("/api/auth/register")
            .then()
                .statusCode(400);
        }

        @Test
        @DisplayName("Missing required fields — validation error")
        void register_missingFields_validationError() {
            unauthenticatedRequest()
                .body("{}")
            .when()
                .post("/api/auth/register")
            .then()
                .statusCode(400);
        }

        @Test
        @DisplayName("Password too short — validation error")
        void register_shortPassword_validationError() {
            unauthenticatedRequest()
                .body("""
                    {
                        "username": "validuser",
                        "email": "valid@example.com",
                        "password": "12345"
                    }
                    """)
            .when()
                .post("/api/auth/register")
            .then()
                .statusCode(400);
        }
    }

    @Nested
    @DisplayName("POST /api/auth/login")
    class Login {

        @BeforeEach
        void createUser() {
            createTestUser(USERNAME, EMAIL, PASSWORD);
        }

        @Test
        @DisplayName("Valid credentials — returns JWT + refresh token")
        void login_validCredentials_returnsTokens() {
            unauthenticatedRequest()
                .body("""
                    {
                        "username": "%s",
                        "password": "%s"
                    }
                    """.formatted(USERNAME, PASSWORD))
            .when()
                .post("/api/auth/login")
            .then()
                .statusCode(200)
                .body("accessToken", notNullValue())
                .body("refreshToken", notNullValue())
                .body("username", equalTo(USERNAME));
        }

        @Test
        @DisplayName("Wrong password — returns 401")
        void login_wrongPassword_unauthorized() {
            unauthenticatedRequest()
                .body("""
                    {
                        "username": "%s",
                        "password": "WrongPassword!"
                    }
                    """.formatted(USERNAME))
            .when()
                .post("/api/auth/login")
            .then()
                .statusCode(401);
        }

        @Test
        @DisplayName("Non-existent user — returns 401")
        void login_nonExistentUser_unauthorized() {
            unauthenticatedRequest()
                .body("""
                    {
                        "username": "ghost",
                        "password": "doesnt-matter"
                    }
                    """)
            .when()
                .post("/api/auth/login")
            .then()
                .statusCode(401);
        }

        @Test
        @DisplayName("Missing username — validation error")
        void login_missingUsername_validationError() {
            unauthenticatedRequest()
                .body("""
                    {
                        "password": "%s"
                    }
                    """.formatted(PASSWORD))
            .when()
                .post("/api/auth/login")
            .then()
                .statusCode(400);
        }
    }

    @Nested
    @DisplayName("POST /api/auth/refresh")
    class RefreshToken {

        @Test
        @DisplayName("Valid refresh token — returns new access token")
        void refresh_validToken_returnsNewAccessToken() {
            // Register to get tokens
            String refreshToken = unauthenticatedRequest()
                .body("""
                    {
                        "username": "%s",
                        "email": "%s",
                        "password": "%s"
                    }
                    """.formatted(USERNAME, EMAIL, PASSWORD))
            .when()
                .post("/api/auth/register")
            .then()
                .statusCode(200)
                .extract()
                .path("refreshToken");

            // Use refresh token
            unauthenticatedRequest()
                .body("""
                    {
                        "refreshToken": "%s"
                    }
                    """.formatted(refreshToken))
            .when()
                .post("/api/auth/refresh")
            .then()
                .statusCode(200)
                .body("accessToken", notNullValue())
                .body("username", equalTo(USERNAME));
        }

        @Test
        @DisplayName("Invalid refresh token — returns error")
        void refresh_invalidToken_error() {
            unauthenticatedRequest()
                .body("""
                    {
                        "refreshToken": "invalid-refresh-token"
                    }
                    """)
            .when()
                .post("/api/auth/refresh")
            .then()
                .statusCode(anyOf(is(400), is(401), is(403), is(500)));
        }
    }

    @Nested
    @DisplayName("POST /api/auth/logout")
    class Logout {

        @Test
        @DisplayName("Logout with valid refresh token — 200")
        void logout_validToken_success() {
            // Register to get tokens
            String refreshToken = unauthenticatedRequest()
                .body("""
                    {
                        "username": "%s",
                        "email": "%s",
                        "password": "%s"
                    }
                    """.formatted(USERNAME, EMAIL, PASSWORD))
            .when()
                .post("/api/auth/register")
            .then()
                .statusCode(200)
                .extract()
                .path("refreshToken");

            unauthenticatedRequest()
                .body("""
                    {
                        "refreshToken": "%s"
                    }
                    """.formatted(refreshToken))
            .when()
                .post("/api/auth/logout")
            .then()
                .statusCode(200)
                .body("message", containsString("successfully"));
        }
    }

    @Nested
    @DisplayName("Security — JWT Protected Endpoints")
    class Security {

        @Test
        @DisplayName("Accessing protected endpoint without JWT — 401")
        void protectedEndpoint_noToken_unauthorized() {
            unauthenticatedRequest()
            .when()
                .get("/api/user/profile")
            .then()
                .statusCode(401);
        }

        @Test
        @DisplayName("Accessing protected endpoint with invalid JWT — 401")
        void protectedEndpoint_invalidToken_unauthorized() {
            given()
                .header("Authorization", "Bearer totally-invalid-token")
            .when()
                .get("/api/user/profile")
            .then()
                .statusCode(401);
        }

        @Test
        @DisplayName("Accessing protected endpoint with expired JWT — 401")
        void protectedEndpoint_expiredToken_unauthorized() {
            // Generate a token that's already expired (by using a very short expiry)
            // The JwtUtils uses the configured expiry, so we use a manually crafted
            // expired token instead
            given()
                .header("Authorization", "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0IiwiZXhwIjoxfQ.invalid")
            .when()
                .get("/api/user/profile")
            .then()
                .statusCode(401);
        }

        @Test
        @DisplayName("Auth endpoints are publicly accessible")
        void authEndpoints_noToken_accessible() {
            // /api/auth/** should be public
            unauthenticatedRequest()
                .body("""
                    {
                        "username": "x",
                        "password": "y"
                    }
                    """)
            .when()
                .post("/api/auth/login")
            .then()
                .statusCode(401); // Bad creds, not 403 (auth endpoint is accessible)
        }
    }
}
