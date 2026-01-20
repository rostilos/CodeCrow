package org.rostilos.codecrow.core.model.user.twofactor;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.user.User;

import java.time.Instant;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;

@DisplayName("TwoFactorAuth Entity Tests")
class TwoFactorAuthTest {

    private TwoFactorAuth twoFactorAuth;

    @BeforeEach
    void setUp() {
        twoFactorAuth = new TwoFactorAuth();
    }

    @Nested
    @DisplayName("Constructor tests")
    class ConstructorTests {

        @Test
        @DisplayName("Default constructor should create object with defaults")
        void defaultConstructorShouldCreateObjectWithDefaults() {
            TwoFactorAuth auth = new TwoFactorAuth();
            
            assertThat(auth.getId()).isNull();
            assertThat(auth.getUser()).isNull();
            assertThat(auth.getTwoFactorType()).isNull();
            assertThat(auth.isEnabled()).isFalse();
            assertThat(auth.isVerified()).isFalse();
        }

        @Test
        @DisplayName("Parameterized constructor should set user and type")
        void parameterizedConstructorShouldSetUserAndType() {
            User user = mock(User.class);
            
            TwoFactorAuth auth = new TwoFactorAuth(user, ETwoFactorType.TOTP);
            
            assertThat(auth.getUser()).isSameAs(user);
            assertThat(auth.getTwoFactorType()).isEqualTo(ETwoFactorType.TOTP);
        }
    }

    @Nested
    @DisplayName("Getter and Setter tests")
    class GetterSetterTests {

        @Test
        @DisplayName("Should set and get id")
        void shouldSetAndGetId() {
            twoFactorAuth.setId(100L);
            assertThat(twoFactorAuth.getId()).isEqualTo(100L);
        }

        @Test
        @DisplayName("Should set and get user")
        void shouldSetAndGetUser() {
            User user = mock(User.class);
            twoFactorAuth.setUser(user);
            assertThat(twoFactorAuth.getUser()).isSameAs(user);
        }

        @Test
        @DisplayName("Should set and get twoFactorType")
        void shouldSetAndGetTwoFactorType() {
            twoFactorAuth.setTwoFactorType(ETwoFactorType.EMAIL);
            assertThat(twoFactorAuth.getTwoFactorType()).isEqualTo(ETwoFactorType.EMAIL);
            
            twoFactorAuth.setTwoFactorType(ETwoFactorType.TOTP);
            assertThat(twoFactorAuth.getTwoFactorType()).isEqualTo(ETwoFactorType.TOTP);
        }

        @Test
        @DisplayName("Should set and get secretKey")
        void shouldSetAndGetSecretKey() {
            twoFactorAuth.setSecretKey("JBSWY3DPEHPK3PXP");
            assertThat(twoFactorAuth.getSecretKey()).isEqualTo("JBSWY3DPEHPK3PXP");
        }

        @Test
        @DisplayName("Should set and get enabled")
        void shouldSetAndGetEnabled() {
            twoFactorAuth.setEnabled(true);
            assertThat(twoFactorAuth.isEnabled()).isTrue();
            
            twoFactorAuth.setEnabled(false);
            assertThat(twoFactorAuth.isEnabled()).isFalse();
        }

        @Test
        @DisplayName("Should set and get verified")
        void shouldSetAndGetVerified() {
            twoFactorAuth.setVerified(true);
            assertThat(twoFactorAuth.isVerified()).isTrue();
            
            twoFactorAuth.setVerified(false);
            assertThat(twoFactorAuth.isVerified()).isFalse();
        }

        @Test
        @DisplayName("Should set and get backupCodes")
        void shouldSetAndGetBackupCodes() {
            String codes = "CODE1,CODE2,CODE3,CODE4,CODE5";
            twoFactorAuth.setBackupCodes(codes);
            assertThat(twoFactorAuth.getBackupCodes()).isEqualTo(codes);
        }

        @Test
        @DisplayName("Should set and get emailCode")
        void shouldSetAndGetEmailCode() {
            twoFactorAuth.setEmailCode("123456");
            assertThat(twoFactorAuth.getEmailCode()).isEqualTo("123456");
        }

        @Test
        @DisplayName("Should set and get emailCodeExpiresAt")
        void shouldSetAndGetEmailCodeExpiresAt() {
            Instant expiry = Instant.now().plusSeconds(300);
            twoFactorAuth.setEmailCodeExpiresAt(expiry);
            assertThat(twoFactorAuth.getEmailCodeExpiresAt()).isEqualTo(expiry);
        }
    }

    @Nested
    @DisplayName("isEmailCodeExpired tests")
    class IsEmailCodeExpiredTests {

        @Test
        @DisplayName("Should return false when emailCodeExpiresAt is null")
        void shouldReturnFalseWhenExpiresAtIsNull() {
            twoFactorAuth.setEmailCodeExpiresAt(null);
            assertThat(twoFactorAuth.isEmailCodeExpired()).isFalse();
        }

        @Test
        @DisplayName("Should return true when email code has expired")
        void shouldReturnTrueWhenEmailCodeHasExpired() {
            Instant pastTime = Instant.now().minusSeconds(60);
            twoFactorAuth.setEmailCodeExpiresAt(pastTime);
            assertThat(twoFactorAuth.isEmailCodeExpired()).isTrue();
        }

        @Test
        @DisplayName("Should return false when email code has not expired")
        void shouldReturnFalseWhenEmailCodeHasNotExpired() {
            Instant futureTime = Instant.now().plusSeconds(300);
            twoFactorAuth.setEmailCodeExpiresAt(futureTime);
            assertThat(twoFactorAuth.isEmailCodeExpired()).isFalse();
        }
    }

    @Nested
    @DisplayName("Initial state tests")
    class InitialStateTests {

        @Test
        @DisplayName("Default enabled should be false")
        void defaultEnabledShouldBeFalse() {
            assertThat(new TwoFactorAuth().isEnabled()).isFalse();
        }

        @Test
        @DisplayName("Default verified should be false")
        void defaultVerifiedShouldBeFalse() {
            assertThat(new TwoFactorAuth().isVerified()).isFalse();
        }
    }
}
