package org.rostilos.codecrow.core.model.user;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.user.status.EStatus;

import java.time.Instant;

import static org.assertj.core.api.Assertions.assertThat;

class RefreshTokenTest {

    @Test
    void shouldCreateWithDefaultConstructor() {
        RefreshToken token = new RefreshToken();
        
        assertThat(token.getId()).isNull();
        assertThat(token.getToken()).isNull();
        assertThat(token.getUser()).isNull();
        assertThat(token.getExpiryDate()).isNull();
        assertThat(token.isRevoked()).isFalse();
        assertThat(token.getCreatedAt()).isNotNull();
    }

    @Test
    void shouldCreateWithParameterizedConstructor() {
        User user = createTestUser();
        String tokenValue = "refresh-token-123";
        Instant expiryDate = Instant.now().plusSeconds(86400);
        Instant beforeCreation = Instant.now();
        
        RefreshToken token = new RefreshToken(tokenValue, user, expiryDate);
        
        assertThat(token.getToken()).isEqualTo(tokenValue);
        assertThat(token.getUser()).isEqualTo(user);
        assertThat(token.getExpiryDate()).isEqualTo(expiryDate);
        assertThat(token.isRevoked()).isFalse();
        assertThat(token.getCreatedAt()).isNotNull();
        assertThat(token.getCreatedAt()).isAfterOrEqualTo(beforeCreation);
    }

    @Test
    void shouldSetAndGetId() {
        RefreshToken token = new RefreshToken();
        Long id = 456L;
        
        token.setId(id);
        
        assertThat(token.getId()).isEqualTo(id);
    }

    @Test
    void shouldSetAndGetToken() {
        RefreshToken token = new RefreshToken();
        String tokenValue = "refresh-token-abc";
        
        token.setToken(tokenValue);
        
        assertThat(token.getToken()).isEqualTo(tokenValue);
    }

    @Test
    void shouldSetAndGetUser() {
        RefreshToken token = new RefreshToken();
        User user = createTestUser();
        
        token.setUser(user);
        
        assertThat(token.getUser()).isEqualTo(user);
    }

    @Test
    void shouldSetAndGetExpiryDate() {
        RefreshToken token = new RefreshToken();
        Instant expiryDate = Instant.now().plusSeconds(86400);
        
        token.setExpiryDate(expiryDate);
        
        assertThat(token.getExpiryDate()).isEqualTo(expiryDate);
    }

    @Test
    void shouldSetAndGetRevoked() {
        RefreshToken token = new RefreshToken();
        
        token.setRevoked(true);
        
        assertThat(token.isRevoked()).isTrue();
    }

    @Test
    void shouldSetAndGetCreatedAt() {
        RefreshToken token = new RefreshToken();
        Instant createdAt = Instant.now().minusSeconds(3600);
        
        token.setCreatedAt(createdAt);
        
        assertThat(token.getCreatedAt()).isEqualTo(createdAt);
    }

    @Test
    void isExpired_shouldReturnTrueWhenExpiryDateIsPast() {
        RefreshToken token = new RefreshToken();
        token.setExpiryDate(Instant.now().minusSeconds(10));
        
        assertThat(token.isExpired()).isTrue();
    }

    @Test
    void isExpired_shouldReturnFalseWhenExpiryDateIsFuture() {
        RefreshToken token = new RefreshToken();
        token.setExpiryDate(Instant.now().plusSeconds(86400));
        
        assertThat(token.isExpired()).isFalse();
    }

    @Test
    void isValid_shouldReturnTrueWhenNotRevokedAndNotExpired() {
        RefreshToken token = new RefreshToken();
        token.setRevoked(false);
        token.setExpiryDate(Instant.now().plusSeconds(86400));
        
        assertThat(token.isValid()).isTrue();
    }

    @Test
    void isValid_shouldReturnFalseWhenRevoked() {
        RefreshToken token = new RefreshToken();
        token.setRevoked(true);
        token.setExpiryDate(Instant.now().plusSeconds(86400));
        
        assertThat(token.isValid()).isFalse();
    }

    @Test
    void isValid_shouldReturnFalseWhenExpired() {
        RefreshToken token = new RefreshToken();
        token.setRevoked(false);
        token.setExpiryDate(Instant.now().minusSeconds(10));
        
        assertThat(token.isValid()).isFalse();
    }

    @Test
    void isValid_shouldReturnFalseWhenRevokedAndExpired() {
        RefreshToken token = new RefreshToken();
        token.setRevoked(true);
        token.setExpiryDate(Instant.now().minusSeconds(10));
        
        assertThat(token.isValid()).isFalse();
    }

    @Test
    void shouldDefaultRevokedToFalse() {
        RefreshToken token = new RefreshToken();
        
        assertThat(token.isRevoked()).isFalse();
    }

    private User createTestUser() {
        User user = new User();
        user.setUsername("testuser");
        user.setEmail("test@example.com");
        user.setStatus(EStatus.STATUS_ACTIVE);
        return user;
    }
}
