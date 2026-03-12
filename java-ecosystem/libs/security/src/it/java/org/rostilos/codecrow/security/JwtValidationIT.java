package org.rostilos.codecrow.security;

import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.SignatureAlgorithm;
import io.jsonwebtoken.security.Keys;
import org.junit.jupiter.api.*;

import javax.crypto.SecretKey;
import java.nio.charset.StandardCharsets;
import java.util.Date;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * Integration test for JWT token lifecycle: generation, validation, expiry, claims.
 */
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
class JwtValidationIT {

    private static final String SECRET = "a-very-long-secret-key-that-is-at-least-256-bits-long-for-hmac-sha256-signing";
    private SecretKey signingKey;

    @BeforeEach
    void setup() {
        signingKey = Keys.hmacShaKeyFor(SECRET.getBytes(StandardCharsets.UTF_8));
    }

    @Test
    @Order(1)
    @DisplayName("Should generate valid JWT with correct claims")
    void shouldGenerateValidJwt() {
        String token = Jwts.builder()
                .setSubject("user123")
                .setIssuedAt(new Date())
                .setExpiration(new Date(System.currentTimeMillis() + 3600_000))
                .claim("type", "ACCESS")
                .signWith(signingKey, SignatureAlgorithm.HS256)
                .compact();

        assertThat(token).isNotBlank();
        assertThat(token.split("\\.")).hasSize(3); // Header.Payload.Signature

        var claims = Jwts.parserBuilder()
                .setSigningKey(signingKey)
                .build()
                .parseClaimsJws(token)
                .getBody();

        assertThat(claims.getSubject()).isEqualTo("user123");
        assertThat(claims.get("type", String.class)).isEqualTo("ACCESS");
        assertThat(claims.getExpiration()).isAfter(new Date());
    }

    @Test
    @Order(2)
    @DisplayName("Should reject expired JWT")
    void shouldRejectExpiredJwt() {
        String token = Jwts.builder()
                .setSubject("user123")
                .setIssuedAt(new Date(System.currentTimeMillis() - 7200_000))
                .setExpiration(new Date(System.currentTimeMillis() - 3600_000))
                .signWith(signingKey, SignatureAlgorithm.HS256)
                .compact();

        assertThatThrownBy(() ->
                Jwts.parserBuilder()
                        .setSigningKey(signingKey)
                        .build()
                        .parseClaimsJws(token)
        ).isInstanceOf(io.jsonwebtoken.ExpiredJwtException.class);
    }

    @Test
    @Order(3)
    @DisplayName("Should reject JWT with wrong signing key")
    void shouldRejectWrongSigningKey() {
        String token = Jwts.builder()
                .setSubject("user123")
                .setExpiration(new Date(System.currentTimeMillis() + 3600_000))
                .signWith(signingKey, SignatureAlgorithm.HS256)
                .compact();

        SecretKey wrongKey = Keys.hmacShaKeyFor(
                "wrong-secret-key-that-is-also-at-least-256-bits-long-for-hmac-sha256-signing"
                        .getBytes(StandardCharsets.UTF_8));

        assertThatThrownBy(() ->
                Jwts.parserBuilder()
                        .setSigningKey(wrongKey)
                        .build()
                        .parseClaimsJws(token)
        ).isInstanceOf(io.jsonwebtoken.security.SignatureException.class);
    }

    @Test
    @Order(4)
    @DisplayName("Should reject malformed JWT")
    void shouldRejectMalformedJwt() {
        assertThatThrownBy(() ->
                Jwts.parserBuilder()
                        .setSigningKey(signingKey)
                        .build()
                        .parseClaimsJws("not.a.valid.jwt.token")
        ).isInstanceOf(io.jsonwebtoken.MalformedJwtException.class);
    }

    @Test
    @Order(5)
    @DisplayName("Should handle refresh token with different expiry")
    void shouldHandleRefreshToken() {
        long accessExpiry = 15 * 60 * 1000L; // 15 min
        long refreshExpiry = 7 * 24 * 3600 * 1000L; // 7 days

        String accessToken = Jwts.builder()
                .setSubject("user123")
                .claim("type", "ACCESS")
                .setExpiration(new Date(System.currentTimeMillis() + accessExpiry))
                .signWith(signingKey, SignatureAlgorithm.HS256)
                .compact();

        String refreshToken = Jwts.builder()
                .setSubject("user123")
                .claim("type", "REFRESH")
                .setExpiration(new Date(System.currentTimeMillis() + refreshExpiry))
                .signWith(signingKey, SignatureAlgorithm.HS256)
                .compact();

        var accessClaims = Jwts.parserBuilder()
                .setSigningKey(signingKey).build()
                .parseClaimsJws(accessToken).getBody();
        var refreshClaims = Jwts.parserBuilder()
                .setSigningKey(signingKey).build()
                .parseClaimsJws(refreshToken).getBody();

        assertThat(accessClaims.get("type")).isEqualTo("ACCESS");
        assertThat(refreshClaims.get("type")).isEqualTo("REFRESH");
        assertThat(refreshClaims.getExpiration()).isAfter(accessClaims.getExpiration());
    }

    @Test
    @Order(6)
    @DisplayName("Should handle 2FA claim in JWT")
    void shouldHandle2FAClaim() {
        String token = Jwts.builder()
                .setSubject("user123")
                .claim("twoFactorAuthenticated", true)
                .claim("type", "ACCESS")
                .setExpiration(new Date(System.currentTimeMillis() + 3600_000))
                .signWith(signingKey, SignatureAlgorithm.HS256)
                .compact();

        var claims = Jwts.parserBuilder()
                .setSigningKey(signingKey).build()
                .parseClaimsJws(token).getBody();

        assertThat(claims.get("twoFactorAuthenticated", Boolean.class)).isTrue();
    }

    @Test
    @Order(7)
    @DisplayName("Should support pipeline agent JWT with projectId as subject")
    void shouldSupportPipelineAgentJwt() {
        String token = Jwts.builder()
                .setSubject("project-uuid-12345")
                .claim("type", "PIPELINE_AGENT")
                .setExpiration(new Date(System.currentTimeMillis() + 3600_000))
                .signWith(signingKey, SignatureAlgorithm.HS256)
                .compact();

        var claims = Jwts.parserBuilder()
                .setSigningKey(signingKey).build()
                .parseClaimsJws(token).getBody();

        assertThat(claims.getSubject()).isEqualTo("project-uuid-12345");
        assertThat(claims.get("type")).isEqualTo("PIPELINE_AGENT");
    }
}
