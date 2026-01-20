package org.rostilos.codecrow.security.jwt.utils;

import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.SignatureAlgorithm;
import io.jsonwebtoken.io.Decoders;
import io.jsonwebtoken.security.Keys;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.security.service.UserDetailsImpl;
import org.springframework.security.core.Authentication;

import java.lang.reflect.Field;
import java.security.Key;
import java.util.Base64;
import java.util.Collections;
import java.util.Date;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

@DisplayName("JwtUtils")
class JwtUtilsTest {

    // Valid Base64-encoded secret for HS256 (minimum 256 bits = 32 bytes)
    private static final String TEST_SECRET = Base64.getEncoder().encodeToString(
            "this-is-a-secret-key-for-testing-purposes-only-32b".getBytes());
    private static final int TEST_EXPIRATION_MS = 3600000; // 1 hour
    private static final long TEST_REFRESH_EXPIRATION_MS = 604800000L; // 7 days
    private static final long TEST_PROJECT_EXPIRATION_MS = 86400000L; // 1 day

    private JwtUtils jwtUtils;

    @BeforeEach
    void setUp() throws Exception {
        jwtUtils = new JwtUtils();
        setField(jwtUtils, "jwtSecret", TEST_SECRET);
        setField(jwtUtils, "jwtExpirationMs", TEST_EXPIRATION_MS);
        setField(jwtUtils, "refreshTokenExpirationMs", TEST_REFRESH_EXPIRATION_MS);
        setField(jwtUtils, "projectJwtExpirationMs", TEST_PROJECT_EXPIRATION_MS);
    }

    private void setField(Object target, String fieldName, Object value) throws Exception {
        Field field = target.getClass().getDeclaredField(fieldName);
        field.setAccessible(true);
        field.set(target, value);
    }

    private Key getKey() {
        return Keys.hmacShaKeyFor(Decoders.BASE64.decode(TEST_SECRET));
    }

    @Nested
    @DisplayName("generateJwtToken()")
    class GenerateJwtTokenTests {

        @Test
        @DisplayName("should generate valid JWT token from authentication")
        void shouldGenerateValidJwtFromAuthentication() {
            Authentication auth = mock(Authentication.class);
            UserDetailsImpl userDetails = new UserDetailsImpl(1L, "testuser", "test@example.com", 
                    "password", null, Collections.emptyList());
            when(auth.getPrincipal()).thenReturn(userDetails);

            String token = jwtUtils.generateJwtToken(auth);

            assertThat(token).isNotNull();
            assertThat(token.split("\\.")).hasSize(3); // JWT has 3 parts
        }

        @Test
        @DisplayName("should set correct subject in token")
        void shouldSetCorrectSubjectInToken() {
            Authentication auth = mock(Authentication.class);
            UserDetailsImpl userDetails = new UserDetailsImpl(1L, "testuser", "test@example.com", 
                    "password", null, Collections.emptyList());
            when(auth.getPrincipal()).thenReturn(userDetails);

            String token = jwtUtils.generateJwtToken(auth);
            String username = jwtUtils.getUserNameFromJwtToken(token);

            assertThat(username).isEqualTo("testuser");
        }
    }

    @Nested
    @DisplayName("generateJwtTokenForUser()")
    class GenerateJwtTokenForUserTests {

        @Test
        @DisplayName("should generate token for user without authentication")
        void shouldGenerateTokenForUserWithoutAuth() {
            String token = jwtUtils.generateJwtTokenForUser(123L, "testuser");

            assertThat(token).isNotNull();
            assertThat(jwtUtils.getUserNameFromJwtToken(token)).isEqualTo("testuser");
        }

        @Test
        @DisplayName("should include userId claim")
        void shouldIncludeUserIdClaim() {
            String token = jwtUtils.generateJwtTokenForUser(123L, "testuser");

            var claims = Jwts.parserBuilder().setSigningKey(getKey()).build()
                    .parseClaimsJws(token).getBody();

            assertThat(claims.get("userId", Long.class)).isEqualTo(123L);
        }
    }

    @Nested
    @DisplayName("generateRefreshToken()")
    class GenerateRefreshTokenTests {

        @Test
        @DisplayName("should generate refresh token with type claim")
        void shouldGenerateRefreshTokenWithTypeClaim() {
            String token = jwtUtils.generateRefreshToken(123L, "testuser");

            var claims = Jwts.parserBuilder().setSigningKey(getKey()).build()
                    .parseClaimsJws(token).getBody();

            assertThat(claims.get("type", String.class)).isEqualTo("refresh");
        }

        @Test
        @DisplayName("should include userId in refresh token")
        void shouldIncludeUserIdInRefreshToken() {
            String token = jwtUtils.generateRefreshToken(456L, "testuser");

            Long userId = jwtUtils.getUserIdFromRefreshToken(token);

            assertThat(userId).isEqualTo(456L);
        }
    }

    @Nested
    @DisplayName("validateRefreshToken()")
    class ValidateRefreshTokenTests {

        @Test
        @DisplayName("should return true for valid refresh token")
        void shouldReturnTrueForValidRefreshToken() {
            String token = jwtUtils.generateRefreshToken(123L, "testuser");

            boolean isValid = jwtUtils.validateRefreshToken(token);

            assertThat(isValid).isTrue();
        }

        @Test
        @DisplayName("should return false for regular JWT token")
        void shouldReturnFalseForRegularJwtToken() {
            String token = jwtUtils.generateJwtTokenForUser(123L, "testuser");

            boolean isValid = jwtUtils.validateRefreshToken(token);

            assertThat(isValid).isFalse();
        }

        @Test
        @DisplayName("should return false for invalid token")
        void shouldReturnFalseForInvalidToken() {
            boolean isValid = jwtUtils.validateRefreshToken("invalid.token.here");

            assertThat(isValid).isFalse();
        }
    }

    @Nested
    @DisplayName("getUserIdFromRefreshToken()")
    class GetUserIdFromRefreshTokenTests {

        @Test
        @DisplayName("should extract userId from refresh token")
        void shouldExtractUserIdFromRefreshToken() {
            String token = jwtUtils.generateRefreshToken(789L, "testuser");

            Long userId = jwtUtils.getUserIdFromRefreshToken(token);

            assertThat(userId).isEqualTo(789L);
        }

        @Test
        @DisplayName("should throw exception for non-refresh token")
        void shouldThrowExceptionForNonRefreshToken() {
            String regularToken = jwtUtils.generateJwtTokenForUser(123L, "testuser");

            assertThatThrownBy(() -> jwtUtils.getUserIdFromRefreshToken(regularToken))
                    .isInstanceOf(IllegalArgumentException.class)
                    .hasMessage("Invalid refresh token type");
        }
    }

    @Nested
    @DisplayName("generateTempToken()")
    class GenerateTempTokenTests {

        @Test
        @DisplayName("should generate temp token with 2fa_temp type")
        void shouldGenerateTempTokenWithCorrectType() {
            String token = jwtUtils.generateTempToken(123L, "testuser");

            var claims = Jwts.parserBuilder().setSigningKey(getKey()).build()
                    .parseClaimsJws(token).getBody();

            assertThat(claims.get("type", String.class)).isEqualTo("2fa_temp");
        }

        @Test
        @DisplayName("should include userId in temp token")
        void shouldIncludeUserIdInTempToken() {
            String token = jwtUtils.generateTempToken(555L, "testuser");

            Long userId = jwtUtils.getUserIdFromTempToken(token);

            assertThat(userId).isEqualTo(555L);
        }
    }

    @Nested
    @DisplayName("validateTempToken()")
    class ValidateTempTokenTests {

        @Test
        @DisplayName("should return true for valid temp token")
        void shouldReturnTrueForValidTempToken() {
            String token = jwtUtils.generateTempToken(123L, "testuser");

            boolean isValid = jwtUtils.validateTempToken(token);

            assertThat(isValid).isTrue();
        }

        @Test
        @DisplayName("should return false for refresh token")
        void shouldReturnFalseForRefreshToken() {
            String token = jwtUtils.generateRefreshToken(123L, "testuser");

            boolean isValid = jwtUtils.validateTempToken(token);

            assertThat(isValid).isFalse();
        }

        @Test
        @DisplayName("should return false for invalid token")
        void shouldReturnFalseForInvalidToken() {
            boolean isValid = jwtUtils.validateTempToken("not.valid.token");

            assertThat(isValid).isFalse();
        }
    }

    @Nested
    @DisplayName("getUsernameFromTempToken()")
    class GetUsernameFromTempTokenTests {

        @Test
        @DisplayName("should extract username from temp token")
        void shouldExtractUsernameFromTempToken() {
            String token = jwtUtils.generateTempToken(123L, "testuser");

            String username = jwtUtils.getUsernameFromTempToken(token);

            assertThat(username).isEqualTo("testuser");
        }
    }

    @Nested
    @DisplayName("generateJwtTokenForProjectWithUser()")
    class GenerateJwtTokenForProjectTests {

        @Test
        @DisplayName("should generate token with project as subject")
        void shouldGenerateTokenWithProjectAsSubject() {
            Date expiry = new Date(System.currentTimeMillis() + 3600000);
            String token = jwtUtils.generateJwtTokenForProjectWithUser("project-123", "user-456", expiry);

            String subject = jwtUtils.getUserNameFromJwtToken(token);

            assertThat(subject).isEqualTo("project-123");
        }

        @Test
        @DisplayName("should include userId claim")
        void shouldIncludeUserIdClaim() {
            Date expiry = new Date(System.currentTimeMillis() + 3600000);
            String token = jwtUtils.generateJwtTokenForProjectWithUser("project-123", "user-456", expiry);

            var claims = Jwts.parserBuilder().setSigningKey(getKey()).build()
                    .parseClaimsJws(token).getBody();

            assertThat(claims.get("userId", String.class)).isEqualTo("user-456");
        }

        @Test
        @DisplayName("should respect custom expiration date")
        void shouldRespectCustomExpirationDate() {
            Date customExpiry = new Date(System.currentTimeMillis() + 7200000); // 2 hours
            String token = jwtUtils.generateJwtTokenForProjectWithUser("project-123", "user-456", customExpiry);

            var claims = Jwts.parserBuilder().setSigningKey(getKey()).build()
                    .parseClaimsJws(token).getBody();

            // Allow 1 second tolerance
            assertThat(claims.getExpiration().getTime()).isCloseTo(customExpiry.getTime(), 
                    org.assertj.core.data.Offset.offset(1000L));
        }
    }

    @Nested
    @DisplayName("getUserNameFromJwtToken()")
    class GetUserNameFromJwtTokenTests {

        @Test
        @DisplayName("should extract username from valid token")
        void shouldExtractUsernameFromValidToken() {
            String token = jwtUtils.generateJwtTokenForUser(123L, "extractme");

            String username = jwtUtils.getUserNameFromJwtToken(token);

            assertThat(username).isEqualTo("extractme");
        }
    }

    @Nested
    @DisplayName("validateJwtToken()")
    class ValidateJwtTokenTests {

        @Test
        @DisplayName("should return true for valid token")
        void shouldReturnTrueForValidToken() {
            String token = jwtUtils.generateJwtTokenForUser(123L, "testuser");

            boolean isValid = jwtUtils.validateJwtToken(token);

            assertThat(isValid).isTrue();
        }

        @Test
        @DisplayName("should return false for malformed token")
        void shouldReturnFalseForMalformedToken() {
            boolean isValid = jwtUtils.validateJwtToken("not.a.valid.jwt");

            assertThat(isValid).isFalse();
        }

        @Test
        @DisplayName("should return false for expired token")
        void shouldReturnFalseForExpiredToken() {
            // Create an expired token manually
            String expiredToken = Jwts.builder()
                    .setSubject("testuser")
                    .setIssuedAt(new Date(System.currentTimeMillis() - 7200000))
                    .setExpiration(new Date(System.currentTimeMillis() - 3600000))
                    .signWith(getKey(), SignatureAlgorithm.HS256)
                    .compact();

            boolean isValid = jwtUtils.validateJwtToken(expiredToken);

            assertThat(isValid).isFalse();
        }

        @Test
        @DisplayName("should return false for empty token")
        void shouldReturnFalseForEmptyToken() {
            boolean isValid = jwtUtils.validateJwtToken("");

            assertThat(isValid).isFalse();
        }
    }

    @Nested
    @DisplayName("getRefreshTokenExpirationMs()")
    class GetRefreshTokenExpirationMsTests {

        @Test
        @DisplayName("should return configured expiration time")
        void shouldReturnConfiguredExpirationTime() {
            long expiration = jwtUtils.getRefreshTokenExpirationMs();

            assertThat(expiration).isEqualTo(TEST_REFRESH_EXPIRATION_MS);
        }
    }
}
