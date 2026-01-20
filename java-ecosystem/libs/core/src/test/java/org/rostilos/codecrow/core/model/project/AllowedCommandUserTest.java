package org.rostilos.codecrow.core.model.project;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;

import java.time.OffsetDateTime;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("AllowedCommandUser")
class AllowedCommandUserTest {

    private AllowedCommandUser allowedUser;
    private Project project;

    @BeforeEach
    void setUp() {
        allowedUser = new AllowedCommandUser();
        project = new Project();
        project.setName("Test Project");
    }

    @Nested
    @DisplayName("Constructors")
    class Constructors {

        @Test
        @DisplayName("should create with default constructor")
        void shouldCreateWithDefaultConstructor() {
            AllowedCommandUser user = new AllowedCommandUser();
            
            assertThat(user.getId()).isNull();
            assertThat(user.getProject()).isNull();
            assertThat(user.isSyncedFromVcs()).isFalse();
            assertThat(user.isEnabled()).isTrue();
        }

        @Test
        @DisplayName("should create with parameterized constructor")
        void shouldCreateWithParameterizedConstructor() {
            AllowedCommandUser user = new AllowedCommandUser(
                    project,
                    EVcsProvider.GITHUB,
                    "user-123",
                    "johndoe"
            );
            
            assertThat(user.getProject()).isEqualTo(project);
            assertThat(user.getVcsProvider()).isEqualTo(EVcsProvider.GITHUB);
            assertThat(user.getVcsUserId()).isEqualTo("user-123");
            assertThat(user.getVcsUsername()).isEqualTo("johndoe");
        }

        @Test
        @DisplayName("should create with Bitbucket provider")
        void shouldCreateWithBitbucketProvider() {
            AllowedCommandUser user = new AllowedCommandUser(
                    project,
                    EVcsProvider.BITBUCKET_CLOUD,
                    "{abc123-def456}",
                    "bitbucket_user"
            );
            
            assertThat(user.getVcsProvider()).isEqualTo(EVcsProvider.BITBUCKET_CLOUD);
        }
    }

    @Nested
    @DisplayName("Default values")
    class DefaultValues {

        @Test
        @DisplayName("should have enabled set to true by default")
        void shouldHaveEnabledSetToTrueByDefault() {
            assertThat(allowedUser.isEnabled()).isTrue();
        }

        @Test
        @DisplayName("should have syncedFromVcs set to false by default")
        void shouldHaveSyncedFromVcsSetToFalseByDefault() {
            assertThat(allowedUser.isSyncedFromVcs()).isFalse();
        }
    }

    @Nested
    @DisplayName("Basic properties")
    class BasicProperties {

        @Test
        @DisplayName("should set and get id")
        void shouldSetAndGetId() {
            UUID id = UUID.randomUUID();
            allowedUser.setId(id);
            assertThat(allowedUser.getId()).isEqualTo(id);
        }

        @Test
        @DisplayName("should set and get project")
        void shouldSetAndGetProject() {
            allowedUser.setProject(project);
            assertThat(allowedUser.getProject()).isEqualTo(project);
        }

        @Test
        @DisplayName("should set and get vcsProvider")
        void shouldSetAndGetVcsProvider() {
            allowedUser.setVcsProvider(EVcsProvider.GITHUB);
            assertThat(allowedUser.getVcsProvider()).isEqualTo(EVcsProvider.GITHUB);
        }

        @Test
        @DisplayName("should set and get vcsUserId")
        void shouldSetAndGetVcsUserId() {
            allowedUser.setVcsUserId("12345");
            assertThat(allowedUser.getVcsUserId()).isEqualTo("12345");
        }

        @Test
        @DisplayName("should set and get vcsUsername")
        void shouldSetAndGetVcsUsername() {
            allowedUser.setVcsUsername("testuser");
            assertThat(allowedUser.getVcsUsername()).isEqualTo("testuser");
        }

        @Test
        @DisplayName("should set and get displayName")
        void shouldSetAndGetDisplayName() {
            allowedUser.setDisplayName("John Doe");
            assertThat(allowedUser.getDisplayName()).isEqualTo("John Doe");
        }

        @Test
        @DisplayName("should set and get avatarUrl")
        void shouldSetAndGetAvatarUrl() {
            allowedUser.setAvatarUrl("https://example.com/avatar.png");
            assertThat(allowedUser.getAvatarUrl()).isEqualTo("https://example.com/avatar.png");
        }

        @Test
        @DisplayName("should set and get repoPermission")
        void shouldSetAndGetRepoPermission() {
            allowedUser.setRepoPermission("admin");
            assertThat(allowedUser.getRepoPermission()).isEqualTo("admin");
        }

        @Test
        @DisplayName("should set and get addedBy")
        void shouldSetAndGetAddedBy() {
            allowedUser.setAddedBy("SYSTEM");
            assertThat(allowedUser.getAddedBy()).isEqualTo("SYSTEM");
        }
    }

    @Nested
    @DisplayName("Flags")
    class Flags {

        @Test
        @DisplayName("should set and get syncedFromVcs")
        void shouldSetAndGetSyncedFromVcs() {
            allowedUser.setSyncedFromVcs(true);
            assertThat(allowedUser.isSyncedFromVcs()).isTrue();
            
            allowedUser.setSyncedFromVcs(false);
            assertThat(allowedUser.isSyncedFromVcs()).isFalse();
        }

        @Test
        @DisplayName("should set and get enabled")
        void shouldSetAndGetEnabled() {
            allowedUser.setEnabled(false);
            assertThat(allowedUser.isEnabled()).isFalse();
            
            allowedUser.setEnabled(true);
            assertThat(allowedUser.isEnabled()).isTrue();
        }
    }

    @Nested
    @DisplayName("Timestamps")
    class Timestamps {

        @Test
        @DisplayName("should set and get createdAt")
        void shouldSetAndGetCreatedAt() {
            OffsetDateTime now = OffsetDateTime.now();
            allowedUser.setCreatedAt(now);
            assertThat(allowedUser.getCreatedAt()).isEqualTo(now);
        }

        @Test
        @DisplayName("should set and get updatedAt")
        void shouldSetAndGetUpdatedAt() {
            OffsetDateTime now = OffsetDateTime.now();
            allowedUser.setUpdatedAt(now);
            assertThat(allowedUser.getUpdatedAt()).isEqualTo(now);
        }

        @Test
        @DisplayName("should set and get lastSyncedAt")
        void shouldSetAndGetLastSyncedAt() {
            OffsetDateTime now = OffsetDateTime.now();
            allowedUser.setLastSyncedAt(now);
            assertThat(allowedUser.getLastSyncedAt()).isEqualTo(now);
        }
    }

    @Nested
    @DisplayName("toString()")
    class ToStringMethod {

        @Test
        @DisplayName("should include key fields in toString")
        void shouldIncludeKeyFieldsInToString() {
            allowedUser.setVcsProvider(EVcsProvider.GITHUB);
            allowedUser.setVcsUserId("user-123");
            allowedUser.setVcsUsername("johndoe");
            allowedUser.setEnabled(true);
            
            String result = allowedUser.toString();
            
            assertThat(result).contains("AllowedCommandUser");
            assertThat(result).contains("vcsProvider=GITHUB");
            assertThat(result).contains("vcsUserId='user-123'");
            assertThat(result).contains("vcsUsername='johndoe'");
            assertThat(result).contains("enabled=true");
        }
    }
}
