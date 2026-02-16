package org.rostilos.codecrow.core.dto.project;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.ProjectAiConnectionBinding;
import org.rostilos.codecrow.core.model.project.config.CommandAuthorizationMode;
import org.rostilos.codecrow.core.model.project.config.CommentCommandsConfig;
import org.rostilos.codecrow.core.model.project.config.InstallationMethod;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.project.config.RagConfig;
import org.rostilos.codecrow.core.model.qualitygate.QualityGate;
import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;

import java.lang.reflect.Field;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("ProjectDTO")
class ProjectDTOTest {

    @Nested
    @DisplayName("record constructor")
    class RecordConstructorTests {

        @Test
        @DisplayName("should create ProjectDTO with all fields")
        void shouldCreateWithAllFields() {
            ProjectDTO.DefaultBranchStats stats = new ProjectDTO.DefaultBranchStats(
                    "main", 10, 3, 4, 2, 1
            );
            ProjectDTO.RagConfigDTO ragConfig = new ProjectDTO.RagConfigDTO(
                    true, "main", null, List.of("*.log"), true, 30
            );
            ProjectDTO.CommentCommandsConfigDTO commandsConfig = new ProjectDTO.CommentCommandsConfigDTO(
                    true, 10, 60, true, List.of("/review", "/fix"), "ANYONE", true
            );

            ProjectDTO dto = new ProjectDTO(
                    1L, "Test Project", "Description", true,
                    10L, "OAUTH_MANUAL", "BITBUCKET_CLOUD",
                    "workspace", "repo-slug",
                    20L, "namespace", "main", "main",
                    100L, stats, ragConfig,
                    true, false, "WEBHOOK",
                    commandsConfig, true, 50L, 200000
            );

            assertThat(dto.id()).isEqualTo(1L);
            assertThat(dto.name()).isEqualTo("Test Project");
            assertThat(dto.description()).isEqualTo("Description");
            assertThat(dto.isActive()).isTrue();
            assertThat(dto.vcsConnectionId()).isEqualTo(10L);
            assertThat(dto.vcsConnectionType()).isEqualTo("OAUTH_MANUAL");
            assertThat(dto.vcsProvider()).isEqualTo("BITBUCKET_CLOUD");
            assertThat(dto.projectVcsWorkspace()).isEqualTo("workspace");
            assertThat(dto.projectVcsRepoSlug()).isEqualTo("repo-slug");
            assertThat(dto.aiConnectionId()).isEqualTo(20L);
            assertThat(dto.namespace()).isEqualTo("namespace");
            assertThat(dto.mainBranch()).isEqualTo("main");
            assertThat(dto.defaultBranch()).isEqualTo("main");
            assertThat(dto.defaultBranchId()).isEqualTo(100L);
            assertThat(dto.defaultBranchStats()).isEqualTo(stats);
            assertThat(dto.ragConfig()).isEqualTo(ragConfig);
            assertThat(dto.prAnalysisEnabled()).isTrue();
            assertThat(dto.branchAnalysisEnabled()).isFalse();
            assertThat(dto.installationMethod()).isEqualTo("WEBHOOK");
            assertThat(dto.commentCommandsConfig()).isEqualTo(commandsConfig);
            assertThat(dto.webhooksConfigured()).isTrue();
            assertThat(dto.qualityGateId()).isEqualTo(50L);
        }

        @Test
        @DisplayName("should create ProjectDTO with null optional fields")
        void shouldCreateWithNullOptionalFields() {
            ProjectDTO dto = new ProjectDTO(
                    1L, "Test", null, true,
                    null, null, null, null, null,
                    null, null, null, null, null, null,
                    null, null, null, null, null, null, null, null
            );

            assertThat(dto.description()).isNull();
            assertThat(dto.vcsConnectionId()).isNull();
            assertThat(dto.aiConnectionId()).isNull();
            assertThat(dto.defaultBranchStats()).isNull();
            assertThat(dto.ragConfig()).isNull();
        }
    }

    @Nested
    @DisplayName("fromProject()")
    class FromProjectTests {

        @Test
        @DisplayName("should convert minimal project")
        void shouldConvertMinimalProject() {
            Project project = new Project();
            setField(project, "id", 1L);
            project.setName("Test Project");
            project.setIsActive(true);

            ProjectDTO dto = ProjectDTO.fromProject(project);

            assertThat(dto.id()).isEqualTo(1L);
            assertThat(dto.name()).isEqualTo("Test Project");
            assertThat(dto.isActive()).isTrue();
            assertThat(dto.vcsConnectionId()).isNull();
            assertThat(dto.aiConnectionId()).isNull();
            assertThat(dto.defaultBranchId()).isNull();
        }

        @Test
        @DisplayName("should convert project with VCS binding")
        void shouldConvertProjectWithVcsBinding() {
            Project project = createProjectWithVcsBinding();

            ProjectDTO dto = ProjectDTO.fromProject(project);

            assertThat(dto.vcsConnectionId()).isEqualTo(10L);
            assertThat(dto.vcsConnectionType()).isEqualTo("OAUTH_MANUAL");
            assertThat(dto.vcsProvider()).isEqualTo("BITBUCKET_CLOUD");
            assertThat(dto.projectVcsWorkspace()).isEqualTo("test-workspace");
            assertThat(dto.projectVcsRepoSlug()).isEqualTo("test-repo");
            assertThat(dto.webhooksConfigured()).isTrue();
        }

        @Test
        @DisplayName("should convert project with AI binding")
        void shouldConvertProjectWithAiBinding() {
            Project project = new Project();
            setField(project, "id", 1L);
            project.setName("Test");
            project.setIsActive(true);

            AIConnection aiConnection = new AIConnection();
            setField(aiConnection, "id", 20L);

            ProjectAiConnectionBinding aiBinding = new ProjectAiConnectionBinding();
            aiBinding.setAiConnection(aiConnection);
            project.setAiConnectionBinding(aiBinding);

            ProjectDTO dto = ProjectDTO.fromProject(project);

            assertThat(dto.aiConnectionId()).isEqualTo(20L);
        }

        @Test
        @DisplayName("should convert project with default branch")
        void shouldConvertProjectWithDefaultBranch() {
            Project project = new Project();
            setField(project, "id", 1L);
            project.setName("Test");
            project.setIsActive(true);

            Branch defaultBranch = new Branch();
            setField(defaultBranch, "id", 100L);
            defaultBranch.setBranchName("main");
            project.setDefaultBranch(defaultBranch);

            ProjectDTO dto = ProjectDTO.fromProject(project);

            assertThat(dto.defaultBranchId()).isEqualTo(100L);
            assertThat(dto.defaultBranch()).isEqualTo("main");
            assertThat(dto.defaultBranchStats()).isNotNull();
            assertThat(dto.defaultBranchStats().branchName()).isEqualTo("main");
        }

        @Test
        @DisplayName("should convert project with full configuration")
        void shouldConvertProjectWithFullConfiguration() {
            Project project = new Project();
            setField(project, "id", 1L);
            project.setName("Test");
            project.setIsActive(true);
            project.setPrAnalysisEnabled(true);
            project.setBranchAnalysisEnabled(false);

            RagConfig ragConfig = new RagConfig(true, "develop", null, List.of("*.log", "build/*"), true, 14);
            CommentCommandsConfig commandsConfig = new CommentCommandsConfig(
                    true, 5, 30, true, List.of("/analyze"),
                    CommandAuthorizationMode.ALLOWED_USERS_ONLY, true
            );
            ProjectConfig config = new ProjectConfig(
                    false, "main", null, ragConfig, true, true, InstallationMethod.WEBHOOK, commandsConfig
            );
            project.setConfiguration(config);

            ProjectDTO dto = ProjectDTO.fromProject(project);

            assertThat(dto.mainBranch()).isEqualTo("main");
            assertThat(dto.prAnalysisEnabled()).isTrue();
            assertThat(dto.branchAnalysisEnabled()).isTrue();
            assertThat(dto.installationMethod()).isEqualTo("WEBHOOK");

            assertThat(dto.ragConfig()).isNotNull();
            assertThat(dto.ragConfig().enabled()).isTrue();
            assertThat(dto.ragConfig().branch()).isEqualTo("develop");
            assertThat(dto.ragConfig().excludePatterns()).containsExactly("*.log", "build/*");
            assertThat(dto.ragConfig().multiBranchEnabled()).isTrue();
            assertThat(dto.ragConfig().branchRetentionDays()).isEqualTo(14);

            assertThat(dto.commentCommandsConfig()).isNotNull();
            assertThat(dto.commentCommandsConfig().enabled()).isTrue();
            assertThat(dto.commentCommandsConfig().rateLimit()).isEqualTo(5);
            assertThat(dto.commentCommandsConfig().rateLimitWindowMinutes()).isEqualTo(30);
        }

        @Test
        @DisplayName("should convert project with quality gate")
        void shouldConvertProjectWithQualityGate() {
            Project project = new Project();
            setField(project, "id", 1L);
            project.setName("Test");
            project.setIsActive(true);

            QualityGate qualityGate = new QualityGate();
            setField(qualityGate, "id", 50L);
            project.setQualityGate(qualityGate);

            ProjectDTO dto = ProjectDTO.fromProject(project);

            assertThat(dto.qualityGateId()).isEqualTo(50L);
        }

        @Test
        @DisplayName("should handle null quality gate")
        void shouldHandleNullQualityGate() {
            Project project = new Project();
            setField(project, "id", 1L);
            project.setName("Test");
            project.setIsActive(true);
            project.setQualityGate(null);

            ProjectDTO dto = ProjectDTO.fromProject(project);

            assertThat(dto.qualityGateId()).isNull();
        }

        @Test
        @DisplayName("should handle webhooks not configured")
        void shouldHandleWebhooksNotConfigured() {
            Project project = new Project();
            setField(project, "id", 1L);
            project.setName("Test");
            project.setIsActive(true);

            VcsConnection connection = new VcsConnection();
            setField(connection, "id", 10L);
            setField(connection, "connectionType", EVcsConnectionType.OAUTH_MANUAL);
            setField(connection, "providerType", EVcsProvider.BITBUCKET_CLOUD);

            VcsRepoBinding vcsRepoBinding = new VcsRepoBinding();
            vcsRepoBinding.setVcsConnection(connection);
            vcsRepoBinding.setExternalNamespace("workspace");
            vcsRepoBinding.setExternalRepoSlug("repo");
            vcsRepoBinding.setWebhooksConfigured(false);
            project.setVcsRepoBinding(vcsRepoBinding);

            ProjectDTO dto = ProjectDTO.fromProject(project);

            assertThat(dto.webhooksConfigured()).isFalse();
        }
    }

    @Nested
    @DisplayName("DefaultBranchStats")
    class DefaultBranchStatsTests {

        @Test
        @DisplayName("should create with all fields")
        void shouldCreateWithAllFields() {
            ProjectDTO.DefaultBranchStats stats = new ProjectDTO.DefaultBranchStats(
                    "main", 100, 25, 35, 30, 10
            );

            assertThat(stats.branchName()).isEqualTo("main");
            assertThat(stats.totalIssues()).isEqualTo(100);
            assertThat(stats.highSeverityCount()).isEqualTo(25);
            assertThat(stats.mediumSeverityCount()).isEqualTo(35);
            assertThat(stats.lowSeverityCount()).isEqualTo(30);
            assertThat(stats.resolvedCount()).isEqualTo(10);
        }

        @Test
        @DisplayName("should support zero values")
        void shouldSupportZeroValues() {
            ProjectDTO.DefaultBranchStats stats = new ProjectDTO.DefaultBranchStats(
                    "empty-branch", 0, 0, 0, 0, 0
            );

            assertThat(stats.totalIssues()).isZero();
            assertThat(stats.highSeverityCount()).isZero();
        }
    }

    @Nested
    @DisplayName("RagConfigDTO")
    class RagConfigDTOTests {

        @Test
        @DisplayName("should create with all fields using full constructor")
        void shouldCreateWithAllFieldsUsingFullConstructor() {
            ProjectDTO.RagConfigDTO config = new ProjectDTO.RagConfigDTO(
                    true, "main", null, List.of("*.log", "build/*"), true, 30
            );

            assertThat(config.enabled()).isTrue();
            assertThat(config.branch()).isEqualTo("main");
            assertThat(config.excludePatterns()).containsExactly("*.log", "build/*");
            assertThat(config.multiBranchEnabled()).isTrue();
            assertThat(config.branchRetentionDays()).isEqualTo(30);
        }

        @Test
        @DisplayName("should create with backward-compatible constructor")
        void shouldCreateWithBackwardCompatibleConstructor() {
            ProjectDTO.RagConfigDTO config = new ProjectDTO.RagConfigDTO(
                    true, "develop", List.of("*.tmp")
            );

            assertThat(config.enabled()).isTrue();
            assertThat(config.branch()).isEqualTo("develop");
            assertThat(config.excludePatterns()).containsExactly("*.tmp");
            assertThat(config.multiBranchEnabled()).isNull();
            assertThat(config.branchRetentionDays()).isNull();
        }

        @Test
        @DisplayName("should handle disabled RAG")
        void shouldHandleDisabledRag() {
            ProjectDTO.RagConfigDTO config = new ProjectDTO.RagConfigDTO(
                    false, null, null, null, null, null
            );

            assertThat(config.enabled()).isFalse();
            assertThat(config.branch()).isNull();
            assertThat(config.excludePatterns()).isNull();
        }

        @Test
        @DisplayName("should handle empty exclude patterns")
        void shouldHandleEmptyExcludePatterns() {
            ProjectDTO.RagConfigDTO config = new ProjectDTO.RagConfigDTO(
                    true, "main", null, List.of(), true, 7
            );

            assertThat(config.excludePatterns()).isEmpty();
        }
    }

    @Nested
    @DisplayName("CommentCommandsConfigDTO")
    class CommentCommandsConfigDTOTests {

        @Test
        @DisplayName("should create with all fields")
        void shouldCreateWithAllFields() {
            ProjectDTO.CommentCommandsConfigDTO config = new ProjectDTO.CommentCommandsConfigDTO(
                    true, 10, 60, true, List.of("/review", "/fix", "/ignore"),
                    "ALLOWED_USERS_ONLY", true
            );

            assertThat(config.enabled()).isTrue();
            assertThat(config.rateLimit()).isEqualTo(10);
            assertThat(config.rateLimitWindowMinutes()).isEqualTo(60);
            assertThat(config.allowPublicRepoCommands()).isTrue();
            assertThat(config.allowedCommands()).containsExactly("/review", "/fix", "/ignore");
            assertThat(config.authorizationMode()).isEqualTo("ALLOWED_USERS_ONLY");
            assertThat(config.allowPrAuthor()).isTrue();
        }

        @Test
        @DisplayName("should create from null config")
        void shouldCreateFromNullConfig() {
            ProjectDTO.CommentCommandsConfigDTO dto = ProjectDTO.CommentCommandsConfigDTO.fromConfig(null);

            assertThat(dto.enabled()).isFalse();
            assertThat(dto.rateLimit()).isNull();
            assertThat(dto.rateLimitWindowMinutes()).isNull();
            assertThat(dto.allowPublicRepoCommands()).isNull();
            assertThat(dto.allowedCommands()).isNull();
            assertThat(dto.authorizationMode()).isNull();
            assertThat(dto.allowPrAuthor()).isNull();
        }

        @Test
        @DisplayName("should create from valid config")
        void shouldCreateFromValidConfig() {
            CommentCommandsConfig config = new CommentCommandsConfig(
                    true, 5, 30, false, List.of("/analyze"),
                    CommandAuthorizationMode.PR_AUTHOR_ONLY, false
            );

            ProjectDTO.CommentCommandsConfigDTO dto = ProjectDTO.CommentCommandsConfigDTO.fromConfig(config);

            assertThat(dto.enabled()).isTrue();
            assertThat(dto.rateLimit()).isEqualTo(5);
            assertThat(dto.rateLimitWindowMinutes()).isEqualTo(30);
            assertThat(dto.allowPublicRepoCommands()).isFalse();
            assertThat(dto.allowedCommands()).containsExactly("/analyze");
            assertThat(dto.authorizationMode()).isEqualTo("PR_AUTHOR_ONLY");
            assertThat(dto.allowPrAuthor()).isFalse();
        }

        @Test
        @DisplayName("should use default authorization mode when null")
        void shouldUseDefaultAuthorizationModeWhenNull() {
            CommentCommandsConfig config = new CommentCommandsConfig(
                    true, 5, 30, false, List.of("/test"),
                    null, true
            );

            ProjectDTO.CommentCommandsConfigDTO dto = ProjectDTO.CommentCommandsConfigDTO.fromConfig(config);

            assertThat(dto.authorizationMode()).isEqualTo(CommentCommandsConfig.DEFAULT_AUTHORIZATION_MODE.name());
        }

        @Test
        @DisplayName("should handle disabled commands config")
        void shouldHandleDisabledCommandsConfig() {
            ProjectDTO.CommentCommandsConfigDTO config = new ProjectDTO.CommentCommandsConfigDTO(
                    false, null, null, null, null, null, null
            );

            assertThat(config.enabled()).isFalse();
            assertThat(config.rateLimit()).isNull();
        }
    }

    // Helper methods

    private Project createProjectWithVcsBinding() {
        Project project = new Project();
        setField(project, "id", 1L);
        project.setName("Test");
        project.setIsActive(true);

        VcsConnection connection = new VcsConnection();
        setField(connection, "id", 10L);
        setField(connection, "connectionType", EVcsConnectionType.OAUTH_MANUAL);
        setField(connection, "providerType", EVcsProvider.BITBUCKET_CLOUD);

        VcsRepoBinding vcsRepoBinding = new VcsRepoBinding();
        vcsRepoBinding.setVcsConnection(connection);
        vcsRepoBinding.setExternalNamespace("test-workspace");
        vcsRepoBinding.setExternalRepoSlug("test-repo");
        vcsRepoBinding.setWebhooksConfigured(true);
        project.setVcsRepoBinding(vcsRepoBinding);

        return project;
    }

    private void setField(Object obj, String fieldName, Object value) {
        try {
            Field field = obj.getClass().getDeclaredField(fieldName);
            field.setAccessible(true);
            field.set(obj, value);
        } catch (Exception e) {
            throw new RuntimeException("Failed to set field " + fieldName, e);
        }
    }
}
