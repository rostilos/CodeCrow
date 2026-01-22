package org.rostilos.codecrow.vcsclient.model;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("VcsUser")
class VcsUserTest {

    @Nested
    @DisplayName("Record constructor")
    class RecordConstructor {

        @Test
        @DisplayName("should create user with all fields")
        void shouldCreateUserWithAllFields() {
            VcsUser user = new VcsUser(
                    "user-123",
                    "johndoe",
                    "John Doe",
                    "john@example.com",
                    "https://avatars.example.com/john.png",
                    "https://github.com/johndoe"
            );
            
            assertThat(user.id()).isEqualTo("user-123");
            assertThat(user.username()).isEqualTo("johndoe");
            assertThat(user.displayName()).isEqualTo("John Doe");
            assertThat(user.email()).isEqualTo("john@example.com");
            assertThat(user.avatarUrl()).isEqualTo("https://avatars.example.com/john.png");
            assertThat(user.htmlUrl()).isEqualTo("https://github.com/johndoe");
        }

        @Test
        @DisplayName("should handle null optional fields")
        void shouldHandleNullOptionalFields() {
            VcsUser user = new VcsUser(
                    "user-123",
                    "johndoe",
                    null,
                    null,
                    null,
                    null
            );
            
            assertThat(user.id()).isEqualTo("user-123");
            assertThat(user.username()).isEqualTo("johndoe");
            assertThat(user.displayName()).isNull();
            assertThat(user.email()).isNull();
        }
    }

    @Nested
    @DisplayName("minimal()")
    class Minimal {

        @Test
        @DisplayName("should create minimal user")
        void shouldCreateMinimalUser() {
            VcsUser user = VcsUser.minimal("user-123", "johndoe");
            
            assertThat(user.id()).isEqualTo("user-123");
            assertThat(user.username()).isEqualTo("johndoe");
            assertThat(user.displayName()).isEqualTo("johndoe");
            assertThat(user.email()).isNull();
            assertThat(user.avatarUrl()).isNull();
            assertThat(user.htmlUrl()).isNull();
        }
    }

    @Nested
    @DisplayName("Record equality")
    class RecordEquality {

        @Test
        @DisplayName("should be equal for same values")
        void shouldBeEqualForSameValues() {
            VcsUser user1 = VcsUser.minimal("id", "username");
            VcsUser user2 = VcsUser.minimal("id", "username");
            
            assertThat(user1).isEqualTo(user2);
        }

        @Test
        @DisplayName("should have consistent hashCode")
        void shouldHaveConsistentHashCode() {
            VcsUser user1 = VcsUser.minimal("id", "username");
            VcsUser user2 = VcsUser.minimal("id", "username");
            
            assertThat(user1.hashCode()).isEqualTo(user2.hashCode());
        }
    }
}
