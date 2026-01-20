package org.rostilos.codecrow.vcsclient.model;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("VcsWorkspace")
class VcsWorkspaceTest {

    @Nested
    @DisplayName("Record constructor")
    class RecordConstructor {

        @Test
        @DisplayName("should create workspace with all fields")
        void shouldCreateWorkspaceWithAllFields() {
            VcsWorkspace workspace = new VcsWorkspace(
                    "ws-123",
                    "my-org",
                    "My Organization",
                    false,
                    "https://avatars.example.com/org.png",
                    "https://github.com/my-org"
            );
            
            assertThat(workspace.id()).isEqualTo("ws-123");
            assertThat(workspace.slug()).isEqualTo("my-org");
            assertThat(workspace.name()).isEqualTo("My Organization");
            assertThat(workspace.isUser()).isFalse();
            assertThat(workspace.avatarUrl()).isEqualTo("https://avatars.example.com/org.png");
            assertThat(workspace.htmlUrl()).isEqualTo("https://github.com/my-org");
        }

        @Test
        @DisplayName("should handle user workspace")
        void shouldHandleUserWorkspace() {
            VcsWorkspace workspace = new VcsWorkspace(
                    "user-123",
                    "johndoe",
                    "John Doe",
                    true,
                    null,
                    null
            );
            
            assertThat(workspace.isUser()).isTrue();
        }
    }

    @Nested
    @DisplayName("minimal()")
    class Minimal {

        @Test
        @DisplayName("should create minimal workspace")
        void shouldCreateMinimalWorkspace() {
            VcsWorkspace workspace = VcsWorkspace.minimal("ws-123", "my-org");
            
            assertThat(workspace.id()).isEqualTo("ws-123");
            assertThat(workspace.slug()).isEqualTo("my-org");
            assertThat(workspace.name()).isEqualTo("my-org");
            assertThat(workspace.isUser()).isFalse();
            assertThat(workspace.avatarUrl()).isNull();
            assertThat(workspace.htmlUrl()).isNull();
        }
    }

    @Nested
    @DisplayName("Record equality")
    class RecordEquality {

        @Test
        @DisplayName("should be equal for same values")
        void shouldBeEqualForSameValues() {
            VcsWorkspace ws1 = VcsWorkspace.minimal("id", "slug");
            VcsWorkspace ws2 = VcsWorkspace.minimal("id", "slug");
            
            assertThat(ws1).isEqualTo(ws2);
        }

        @Test
        @DisplayName("should have consistent hashCode")
        void shouldHaveConsistentHashCode() {
            VcsWorkspace ws1 = VcsWorkspace.minimal("id", "slug");
            VcsWorkspace ws2 = VcsWorkspace.minimal("id", "slug");
            
            assertThat(ws1.hashCode()).isEqualTo(ws2.hashCode());
        }
    }
}
