package org.rostilos.codecrow.vcsclient.model;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("VcsCollaborator")
class VcsCollaboratorTest {

    @Test
    @DisplayName("should create with all fields")
    void shouldCreateWithAllFields() {
        VcsCollaborator collaborator = new VcsCollaborator(
                "user-123",
                "john_doe",
                "John Doe",
                "https://avatar.url",
                "admin",
                "https://profile.url"
        );
        
        assertThat(collaborator.userId()).isEqualTo("user-123");
        assertThat(collaborator.username()).isEqualTo("john_doe");
        assertThat(collaborator.displayName()).isEqualTo("John Doe");
        assertThat(collaborator.avatarUrl()).isEqualTo("https://avatar.url");
        assertThat(collaborator.permission()).isEqualTo("admin");
        assertThat(collaborator.htmlUrl()).isEqualTo("https://profile.url");
    }

    @Nested
    @DisplayName("hasWriteAccess")
    class HasWriteAccess {

        @ParameterizedTest
        @ValueSource(strings = {"write", "admin", "owner", "maintain", "push", "WRITE", "ADMIN", "OWNER"})
        @DisplayName("should return true for write or higher permissions")
        void shouldReturnTrueForWritePermissions(String permission) {
            VcsCollaborator collaborator = new VcsCollaborator(
                    "id", "user", "User", null, permission, null
            );
            
            assertThat(collaborator.hasWriteAccess()).isTrue();
        }

        @ParameterizedTest
        @ValueSource(strings = {"read", "pull", "viewer", "READ"})
        @DisplayName("should return false for read-only permissions")
        void shouldReturnFalseForReadPermissions(String permission) {
            VcsCollaborator collaborator = new VcsCollaborator(
                    "id", "user", "User", null, permission, null
            );
            
            assertThat(collaborator.hasWriteAccess()).isFalse();
        }

        @Test
        @DisplayName("should return false for null permission")
        void shouldReturnFalseForNullPermission() {
            VcsCollaborator collaborator = new VcsCollaborator(
                    "id", "user", "User", null, null, null
            );
            
            assertThat(collaborator.hasWriteAccess()).isFalse();
        }
    }

    @Nested
    @DisplayName("hasAdminAccess")
    class HasAdminAccess {

        @ParameterizedTest
        @ValueSource(strings = {"admin", "owner", "ADMIN", "OWNER"})
        @DisplayName("should return true for admin permissions")
        void shouldReturnTrueForAdminPermissions(String permission) {
            VcsCollaborator collaborator = new VcsCollaborator(
                    "id", "user", "User", null, permission, null
            );
            
            assertThat(collaborator.hasAdminAccess()).isTrue();
        }

        @ParameterizedTest
        @ValueSource(strings = {"write", "read", "maintain", "push"})
        @DisplayName("should return false for non-admin permissions")
        void shouldReturnFalseForNonAdminPermissions(String permission) {
            VcsCollaborator collaborator = new VcsCollaborator(
                    "id", "user", "User", null, permission, null
            );
            
            assertThat(collaborator.hasAdminAccess()).isFalse();
        }

        @Test
        @DisplayName("should return false for null permission")
        void shouldReturnFalseForNullPermission() {
            VcsCollaborator collaborator = new VcsCollaborator(
                    "id", "user", "User", null, null, null
            );
            
            assertThat(collaborator.hasAdminAccess()).isFalse();
        }
    }
}
