package org.rostilos.codecrow.core.model.user;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.user.status.EStatus;

import java.time.Instant;

import static org.assertj.core.api.Assertions.assertThat;

class PasswordResetTokenTest {

    @Test
    void shouldCreateWithDefaultConstructor() {
        PasswordResetToken token = new PasswordResetToken();
        
        assertThat(token.getId()).isNull();
        assertThat(token.getToken()).isNull();
        assertThat(token.getUser()).isNull();
        assertThat(token.getExpiryDate()).isNull();
        assertThat(token.isUsed()).isFalse();
        assertThat(token.getCreatedAt()).isNotNull();
    }

    @Test
    void shouldCreateWithParameterizedConstructor() {
        User user = createTestUser();
        String tokenValue = "test-token-123";
        Instant beforeCreation = Instant.now();
        
        PasswordResetToken token = new PasswordResetToken(tokenValue, user);
        
        assertThat(token.getToken()).isEqualTo(tokenValue);
        assertThat(token.getUser()).isEqualTo(user);
        assertThat(token.isUsed()).isFalse();
        assertThat(token.getCreatedAt()).isNotNull();
        assertThat(token.getCreatedAt()).isAfterOrEqualTo(beforeCreation);
        assertThat(token.getExpiryDate()).isNotNull();
        assertThat(token.getExpiryDate()).isAfter(token.getCreatedAt());
    }

    @Test
    void shouldSetAndGetId() {
        PasswordResetToken token = new PasswordResetToken();
        Long id = 123L;
        
        token.setId(id);
        
        assertThat(token.getId()).isEqualTo(id);
    }

    @Test
    void shouldSetAndGetToken() {
        PasswordResetToken token = new PasswordResetToken();
        String tokenValue = "reset-token-abc";
        
        token.setToken(tokenValue);
        
        assertThat(token.getToken()).isEqualTo(tokenValue);
    }

    @Test
    void shouldSetAndGetUser() {
        PasswordResetToken token = new PasswordResetToken();
        User user = createTestUser();
        
        token.setUser(user);
        
        assertThat(token.getUser()).isEqualTo(user);
    }

    @Test
    void shouldSetAndGetExpiryDate() {
        PasswordResetToken token = new PasswordResetToken();
        Instant expiryDate = Instant.now().plusSeconds(3600);
        
        token.setExpiryDate(expiryDate);
        
        assertThat(token.getExpiryDate()).isEqualTo(expiryDate);
    }

    @Test
    void shouldSetAndGetUsed() {
        PasswordResetToken token = new PasswordResetToken();
        
        token.setUsed(true);
        
        assertThat(token.isUsed()).isTrue();
    }

    @Test
    void shouldSetAndGetCreatedAt() {
        PasswordResetToken token = new PasswordResetToken();
        Instant createdAt = Instant.now().minusSeconds(3600);
        
        token.setCreatedAt(createdAt);
        
        assertThat(token.getCreatedAt()).isEqualTo(createdAt);
    }

    @Test
    void isExpired_shouldReturnTrueWhenExpiryDateIsPast() {
        PasswordResetToken token = new PasswordResetToken();
        token.setExpiryDate(Instant.now().minusSeconds(10));
        
        assertThat(token.isExpired()).isTrue();
    }

    @Test
    void isExpired_shouldReturnFalseWhenExpiryDateIsFuture() {
        PasswordResetToken token = new PasswordResetToken();
        token.setExpiryDate(Instant.now().plusSeconds(3600));
        
        assertThat(token.isExpired()).isFalse();
    }

    @Test
    void isValid_shouldReturnTrueWhenNotUsedAndNotExpired() {
        PasswordResetToken token = new PasswordResetToken();
        token.setUsed(false);
        token.setExpiryDate(Instant.now().plusSeconds(3600));
        
        assertThat(token.isValid()).isTrue();
    }

    @Test
    void isValid_shouldReturnFalseWhenUsed() {
        PasswordResetToken token = new PasswordResetToken();
        token.setUsed(true);
        token.setExpiryDate(Instant.now().plusSeconds(3600));
        
        assertThat(token.isValid()).isFalse();
    }

    @Test
    void isValid_shouldReturnFalseWhenExpired() {
        PasswordResetToken token = new PasswordResetToken();
        token.setUsed(false);
        token.setExpiryDate(Instant.now().minusSeconds(10));
        
        assertThat(token.isValid()).isFalse();
    }

    @Test
    void isValid_shouldReturnFalseWhenUsedAndExpired() {
        PasswordResetToken token = new PasswordResetToken();
        token.setUsed(true);
        token.setExpiryDate(Instant.now().minusSeconds(10));
        
        assertThat(token.isValid()).isFalse();
    }

    @Test
    void shouldSetExpiryDateOneHourAfterCreation() {
        User user = createTestUser();
        String tokenValue = "test-token";
        
        PasswordResetToken token = new PasswordResetToken(tokenValue, user);
        
        long hourInSeconds = 3600;
        long difference = token.getExpiryDate().getEpochSecond() - token.getCreatedAt().getEpochSecond();
        assertThat(difference).isBetween(hourInSeconds - 2, hourInSeconds + 2);
    }

    private User createTestUser() {
        User user = new User();
        user.setUsername("testuser");
        user.setEmail("test@example.com");
        user.setStatus(EStatus.STATUS_ACTIVE);
        return user;
    }
}
