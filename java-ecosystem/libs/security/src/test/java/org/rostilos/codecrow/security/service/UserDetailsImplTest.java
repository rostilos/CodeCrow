package org.rostilos.codecrow.security.service;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.user.account_type.EAccountType;
import org.rostilos.codecrow.core.model.user.User;
import org.springframework.security.core.GrantedAuthority;

import java.util.Collection;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("UserDetailsImpl")
class UserDetailsImplTest {

    @Nested
    @DisplayName("Constructor")
    class ConstructorTests {

        @Test
        @DisplayName("should create with all fields")
        void shouldCreateWithAllFields() {
            UserDetailsImpl userDetails = new UserDetailsImpl(
                    1L,
                    "testuser",
                    "test@example.com",
                    "password123",
                    "https://example.com/avatar.png",
                    List.of()
            );
            
            assertThat(userDetails.getId()).isEqualTo(1L);
            assertThat(userDetails.getUsername()).isEqualTo("testuser");
            assertThat(userDetails.getEmail()).isEqualTo("test@example.com");
            assertThat(userDetails.getPassword()).isEqualTo("password123");
            assertThat(userDetails.getAvatarUrl()).isEqualTo("https://example.com/avatar.png");
        }
    }

    @Nested
    @DisplayName("build()")
    class BuildFactory {

        @Test
        @DisplayName("should create UserDetailsImpl from regular user with ROLE_USER")
        void shouldCreateFromRegularUserWithRoleUser() {
            User user = new User();
            user.setUsername("regularuser");
            user.setEmail("user@example.com");
            user.setPassword("secret");
            user.setAvatarUrl("https://example.com/avatar.png");
            user.setAccountType(EAccountType.TYPE_DEFAULT);
            
            UserDetailsImpl userDetails = UserDetailsImpl.build(user);
            
            assertThat(userDetails.getUsername()).isEqualTo("regularuser");
            assertThat(userDetails.getEmail()).isEqualTo("user@example.com");
            assertThat(userDetails.getPassword()).isEqualTo("secret");
            assertThat(userDetails.getAvatarUrl()).isEqualTo("https://example.com/avatar.png");
            
            Collection<? extends GrantedAuthority> authorities = userDetails.getAuthorities();
            assertThat(authorities).hasSize(1);
            assertThat(authorities.iterator().next().getAuthority()).isEqualTo("ROLE_USER");
        }

        @Test
        @DisplayName("should create UserDetailsImpl from admin user with ROLE_ADMIN")
        void shouldCreateFromAdminUserWithRoleAdmin() {
            User user = new User();
            user.setUsername("adminuser");
            user.setEmail("admin@example.com");
            user.setPassword("adminsecret");
            user.setAccountType(EAccountType.TYPE_ADMIN);
            
            UserDetailsImpl userDetails = UserDetailsImpl.build(user);
            
            Collection<? extends GrantedAuthority> authorities = userDetails.getAuthorities();
            assertThat(authorities).hasSize(1);
            assertThat(authorities.iterator().next().getAuthority()).isEqualTo("ROLE_ADMIN");
        }

        @Test
        @DisplayName("should create UserDetailsImpl from pro user with ROLE_USER")
        void shouldCreateFromProUserWithRoleUser() {
            User user = new User();
            user.setUsername("prouser");
            user.setEmail("pro@example.com");
            user.setAccountType(EAccountType.TYPE_PRO);
            
            UserDetailsImpl userDetails = UserDetailsImpl.build(user);
            
            Collection<? extends GrantedAuthority> authorities = userDetails.getAuthorities();
            assertThat(authorities).hasSize(1);
            assertThat(authorities.iterator().next().getAuthority()).isEqualTo("ROLE_USER");
        }
    }

    @Nested
    @DisplayName("UserDetails interface methods")
    class UserDetailsInterfaceMethods {

        @Test
        @DisplayName("should return true for isAccountNonExpired")
        void shouldReturnTrueForIsAccountNonExpired() {
            UserDetailsImpl userDetails = new UserDetailsImpl(
                    1L, "user", "email@test.com", "pass", null, List.of());
            
            assertThat(userDetails.isAccountNonExpired()).isTrue();
        }

        @Test
        @DisplayName("should return true for isAccountNonLocked")
        void shouldReturnTrueForIsAccountNonLocked() {
            UserDetailsImpl userDetails = new UserDetailsImpl(
                    1L, "user", "email@test.com", "pass", null, List.of());
            
            assertThat(userDetails.isAccountNonLocked()).isTrue();
        }

        @Test
        @DisplayName("should return true for isCredentialsNonExpired")
        void shouldReturnTrueForIsCredentialsNonExpired() {
            UserDetailsImpl userDetails = new UserDetailsImpl(
                    1L, "user", "email@test.com", "pass", null, List.of());
            
            assertThat(userDetails.isCredentialsNonExpired()).isTrue();
        }

        @Test
        @DisplayName("should return true for isEnabled")
        void shouldReturnTrueForIsEnabled() {
            UserDetailsImpl userDetails = new UserDetailsImpl(
                    1L, "user", "email@test.com", "pass", null, List.of());
            
            assertThat(userDetails.isEnabled()).isTrue();
        }
    }

    @Nested
    @DisplayName("equals()")
    class EqualsMethod {

        @Test
        @DisplayName("should return true for same instance")
        void shouldReturnTrueForSameInstance() {
            UserDetailsImpl userDetails = new UserDetailsImpl(
                    1L, "user", "email@test.com", "pass", null, List.of());
            
            assertThat(userDetails.equals(userDetails)).isTrue();
        }

        @Test
        @DisplayName("should return true for same id")
        void shouldReturnTrueForSameId() {
            UserDetailsImpl userDetails1 = new UserDetailsImpl(
                    1L, "user1", "email1@test.com", "pass1", null, List.of());
            UserDetailsImpl userDetails2 = new UserDetailsImpl(
                    1L, "user2", "email2@test.com", "pass2", null, List.of());
            
            assertThat(userDetails1.equals(userDetails2)).isTrue();
        }

        @Test
        @DisplayName("should return false for different id")
        void shouldReturnFalseForDifferentId() {
            UserDetailsImpl userDetails1 = new UserDetailsImpl(
                    1L, "user", "email@test.com", "pass", null, List.of());
            UserDetailsImpl userDetails2 = new UserDetailsImpl(
                    2L, "user", "email@test.com", "pass", null, List.of());
            
            assertThat(userDetails1.equals(userDetails2)).isFalse();
        }

        @Test
        @DisplayName("should return false for null")
        void shouldReturnFalseForNull() {
            UserDetailsImpl userDetails = new UserDetailsImpl(
                    1L, "user", "email@test.com", "pass", null, List.of());
            
            assertThat(userDetails.equals(null)).isFalse();
        }

        @Test
        @DisplayName("should return false for different class")
        void shouldReturnFalseForDifferentClass() {
            UserDetailsImpl userDetails = new UserDetailsImpl(
                    1L, "user", "email@test.com", "pass", null, List.of());
            
            assertThat(userDetails.equals("string")).isFalse();
        }
    }
}
