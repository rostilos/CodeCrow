package org.rostilos.codecrow.vcsclient.model;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("VcsRepository")
class VcsRepositoryTest {

    @Nested
    @DisplayName("Record constructor")
    class RecordConstructor {

        @Test
        @DisplayName("should create repository with all fields")
        void shouldCreateRepositoryWithAllFields() {
            VcsRepository repo = new VcsRepository(
                    "repo-123",
                    "my-repo",
                    "My Repository",
                    "org/my-repo",
                    "A test repository",
                    true,
                    "main",
                    "https://github.com/org/my-repo.git",
                    "https://github.com/org/my-repo",
                    "org",
                    "https://avatars.example.com/repo.png"
            );
            
            assertThat(repo.id()).isEqualTo("repo-123");
            assertThat(repo.slug()).isEqualTo("my-repo");
            assertThat(repo.name()).isEqualTo("My Repository");
            assertThat(repo.fullName()).isEqualTo("org/my-repo");
            assertThat(repo.description()).isEqualTo("A test repository");
            assertThat(repo.isPrivate()).isTrue();
            assertThat(repo.defaultBranch()).isEqualTo("main");
            assertThat(repo.cloneUrl()).isEqualTo("https://github.com/org/my-repo.git");
            assertThat(repo.htmlUrl()).isEqualTo("https://github.com/org/my-repo");
            assertThat(repo.namespace()).isEqualTo("org");
            assertThat(repo.avatarUrl()).isEqualTo("https://avatars.example.com/repo.png");
        }

        @Test
        @DisplayName("should handle null optional fields")
        void shouldHandleNullOptionalFields() {
            VcsRepository repo = new VcsRepository(
                    "repo-123",
                    "my-repo",
                    null,
                    null,
                    null,
                    false,
                    null,
                    null,
                    null,
                    "org",
                    null
            );
            
            assertThat(repo.id()).isEqualTo("repo-123");
            assertThat(repo.slug()).isEqualTo("my-repo");
            assertThat(repo.name()).isNull();
            assertThat(repo.description()).isNull();
            assertThat(repo.isPrivate()).isFalse();
            assertThat(repo.defaultBranch()).isNull();
        }
    }

    @Nested
    @DisplayName("minimal()")
    class Minimal {

        @Test
        @DisplayName("should create minimal repository")
        void shouldCreateMinimalRepository() {
            VcsRepository repo = VcsRepository.minimal("repo-123", "my-repo", "org");
            
            assertThat(repo.id()).isEqualTo("repo-123");
            assertThat(repo.slug()).isEqualTo("my-repo");
            assertThat(repo.name()).isEqualTo("my-repo");
            assertThat(repo.fullName()).isEqualTo("org/my-repo");
            assertThat(repo.namespace()).isEqualTo("org");
            assertThat(repo.isPrivate()).isTrue();
        }

        @Test
        @DisplayName("should set null for optional fields in minimal")
        void shouldSetNullForOptionalFieldsInMinimal() {
            VcsRepository repo = VcsRepository.minimal("id", "slug", "ns");
            
            assertThat(repo.description()).isNull();
            assertThat(repo.defaultBranch()).isNull();
            assertThat(repo.cloneUrl()).isNull();
            assertThat(repo.htmlUrl()).isNull();
            assertThat(repo.avatarUrl()).isNull();
        }
    }

    @Nested
    @DisplayName("Record equality")
    class RecordEquality {

        @Test
        @DisplayName("should be equal for same values")
        void shouldBeEqualForSameValues() {
            VcsRepository repo1 = VcsRepository.minimal("id", "slug", "ns");
            VcsRepository repo2 = VcsRepository.minimal("id", "slug", "ns");
            
            assertThat(repo1).isEqualTo(repo2);
        }

        @Test
        @DisplayName("should not be equal for different values")
        void shouldNotBeEqualForDifferentValues() {
            VcsRepository repo1 = VcsRepository.minimal("id1", "slug", "ns");
            VcsRepository repo2 = VcsRepository.minimal("id2", "slug", "ns");
            
            assertThat(repo1).isNotEqualTo(repo2);
        }

        @Test
        @DisplayName("should have consistent hashCode")
        void shouldHaveConsistentHashCode() {
            VcsRepository repo1 = VcsRepository.minimal("id", "slug", "ns");
            VcsRepository repo2 = VcsRepository.minimal("id", "slug", "ns");
            
            assertThat(repo1.hashCode()).isEqualTo(repo2.hashCode());
        }
    }
}
