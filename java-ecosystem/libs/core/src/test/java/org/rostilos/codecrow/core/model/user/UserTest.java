package org.rostilos.codecrow.core.model.user;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.user.account_type.EAccountType;
import org.rostilos.codecrow.core.model.user.status.EStatus;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("User Entity")
class UserTest {

    @Nested
    @DisplayName("Constructors")
    class Constructors {

        @Test
        @DisplayName("should create user with default constructor")
        void shouldCreateUserWithDefaultConstructor() {
            User user = new User();
            
            assertThat(user.getId()).isNull();
            assertThat(user.getUsername()).isNull();
            assertThat(user.getEmail()).isNull();
            assertThat(user.getPassword()).isNull();
            assertThat(user.getCompany()).isNull();
            assertThat(user.getStatus()).isEqualTo(EStatus.STATUS_ACTIVE);
            assertThat(user.getAccountType()).isEqualTo(EAccountType.TYPE_ADMIN);
        }

        @Test
        @DisplayName("should create user with parameterized constructor")
        void shouldCreateUserWithParameterizedConstructor() {
            User user = new User("testuser", "test@example.com", "password123", "TestCorp");
            
            assertThat(user.getUsername()).isEqualTo("testuser");
            assertThat(user.getEmail()).isEqualTo("test@example.com");
            assertThat(user.getPassword()).isEqualTo("password123");
            assertThat(user.getCompany()).isEqualTo("TestCorp");
        }
    }

    @Nested
    @DisplayName("Basic Properties")
    class BasicProperties {

        @Test
        @DisplayName("should set and get id")
        void shouldSetAndGetId() {
            User user = new User();
            user.setId(42L);
            
            assertThat(user.getId()).isEqualTo(42L);
        }

        @Test
        @DisplayName("should set and get username")
        void shouldSetAndGetUsername() {
            User user = new User();
            user.setUsername("newuser");
            
            assertThat(user.getUsername()).isEqualTo("newuser");
        }

        @Test
        @DisplayName("should set and get email")
        void shouldSetAndGetEmail() {
            User user = new User();
            user.setEmail("user@example.com");
            
            assertThat(user.getEmail()).isEqualTo("user@example.com");
        }

        @Test
        @DisplayName("should set and get password")
        void shouldSetAndGetPassword() {
            User user = new User();
            user.setPassword("securePassword");
            
            assertThat(user.getPassword()).isEqualTo("securePassword");
        }

        @Test
        @DisplayName("should set and get company")
        void shouldSetAndGetCompany() {
            User user = new User();
            user.setCompany("Acme Inc");
            
            assertThat(user.getCompany()).isEqualTo("Acme Inc");
        }
    }

    @Nested
    @DisplayName("OAuth Properties")
    class OAuthProperties {

        @Test
        @DisplayName("should set and get googleId")
        void shouldSetAndGetGoogleId() {
            User user = new User();
            user.setGoogleId("google-oauth-id-123");
            
            assertThat(user.getGoogleId()).isEqualTo("google-oauth-id-123");
        }

        @Test
        @DisplayName("should set and get avatarUrl")
        void shouldSetAndGetAvatarUrl() {
            User user = new User();
            user.setAvatarUrl("https://example.com/avatar.png");
            
            assertThat(user.getAvatarUrl()).isEqualTo("https://example.com/avatar.png");
        }
    }

    @Nested
    @DisplayName("Status")
    class Status {

        @Test
        @DisplayName("should have active status by default")
        void shouldHaveActiveStatusByDefault() {
            User user = new User();
            
            assertThat(user.getStatus()).isEqualTo(EStatus.STATUS_ACTIVE);
        }

        @Test
        @DisplayName("should set and get status")
        void shouldSetAndGetStatus() {
            User user = new User();
            user.setStatus(EStatus.STATUS_DISABLED);
            
            assertThat(user.getStatus()).isEqualTo(EStatus.STATUS_DISABLED);
        }
    }

    @Nested
    @DisplayName("Account Type")
    class AccountTypeTests {

        @Test
        @DisplayName("should have admin account type by default")
        void shouldHaveAdminAccountTypeByDefault() {
            User user = new User();
            
            assertThat(user.getAccountType()).isEqualTo(EAccountType.TYPE_ADMIN);
        }

        @Test
        @DisplayName("should set and get account type")
        void shouldSetAndGetAccountType() {
            User user = new User();
            user.setAccountType(EAccountType.TYPE_PRO);
            
            assertThat(user.getAccountType()).isEqualTo(EAccountType.TYPE_PRO);
        }
    }

    @Nested
    @DisplayName("Audit Fields")
    class AuditFields {

        @Test
        @DisplayName("should get created at")
        void shouldGetCreatedAt() {
            User user = new User();
            // createdAt is set by JPA auditing, so it will be null without persistence context
            assertThat(user.getCreatedAt()).isNull();
        }
    }
}
