package org.rostilos.codecrow.core.model.project.config;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("ProjectConfig")
class ProjectConfigTest {

    @Nested
    @DisplayName("Default Constructor")
    class DefaultConstructorTests {

        @Test
        @DisplayName("should default useLocalMcp to false")
        void shouldDefaultUseLocalMcpToFalse() {
            ProjectConfig config = new ProjectConfig();
            assertThat(config.useLocalMcp()).isFalse();
        }

        @Test
        @DisplayName("should default prAnalysisEnabled to true")
        void shouldDefaultPrAnalysisEnabledToTrue() {
            ProjectConfig config = new ProjectConfig();
            assertThat(config.prAnalysisEnabled()).isTrue();
        }

        @Test
        @DisplayName("should default branchAnalysisEnabled to true")
        void shouldDefaultBranchAnalysisEnabledToTrue() {
            ProjectConfig config = new ProjectConfig();
            assertThat(config.branchAnalysisEnabled()).isTrue();
        }
    }

    @Nested
    @DisplayName("Constructors with Parameters")
    class ConstructorsWithParameters {

        @Test
        @DisplayName("should create with useLocalMcp and mainBranch")
        void shouldCreateWithTwoParams() {
            ProjectConfig config = new ProjectConfig(true, "develop");
            assertThat(config.useLocalMcp()).isTrue();
            assertThat(config.mainBranch()).isEqualTo("develop");
        }

        @Test
        @DisplayName("should create with branchAnalysis")
        void shouldCreateWithBranchAnalysis() {
            BranchAnalysisConfig branchConfig = new BranchAnalysisConfig(
                List.of("main"), List.of("feature/*"));
            ProjectConfig config = new ProjectConfig(false, "main", branchConfig);
            
            assertThat(config.branchAnalysis()).isEqualTo(branchConfig);
        }

        @Test
        @DisplayName("should create with ragConfig")
        void shouldCreateWithRagConfig() {
            RagConfig ragConfig = new RagConfig(true, "main", null, List.of("*.log"), true, 30);
            ProjectConfig config = new ProjectConfig(false, "main", null, ragConfig);
            
            assertThat(config.ragConfig()).isEqualTo(ragConfig);
        }

        @Test
        @DisplayName("should create with all parameters")
        void shouldCreateWithAllParams() {
            BranchAnalysisConfig branchConfig = new BranchAnalysisConfig(
                List.of("main"), List.of("*"));
            RagConfig ragConfig = new RagConfig(true, "main", null, List.of(), true, 14);
            CommentCommandsConfig commentConfig = new CommentCommandsConfig();
            
            ProjectConfig config = new ProjectConfig(
                true, "master", branchConfig, ragConfig, 
                false, false, InstallationMethod.WEBHOOK, commentConfig);
            
            assertThat(config.useLocalMcp()).isTrue();
            assertThat(config.mainBranch()).isEqualTo("master");
            assertThat(config.branchAnalysis()).isEqualTo(branchConfig);
            assertThat(config.ragConfig()).isEqualTo(ragConfig);
            assertThat(config.prAnalysisEnabled()).isFalse();
            assertThat(config.branchAnalysisEnabled()).isFalse();
            assertThat(config.installationMethod()).isEqualTo(InstallationMethod.WEBHOOK);
            assertThat(config.commentCommands()).isEqualTo(commentConfig);
        }
    }

    @Nested
    @DisplayName("mainBranch()")
    class MainBranchTests {

        @Test
        @DisplayName("should return mainBranch when set")
        void shouldReturnMainBranchWhenSet() {
            ProjectConfig config = new ProjectConfig(false, "develop");
            assertThat(config.mainBranch()).isEqualTo("develop");
        }

        @Test
        @DisplayName("should fallback to defaultBranch when mainBranch is null")
        void shouldFallbackToDefaultBranch() {
            ProjectConfig config = new ProjectConfig();
            config.setDefaultBranch("legacy-default");
            assertThat(config.mainBranch()).isEqualTo("legacy-default");
        }

        @Test
        @DisplayName("should return 'main' when both are null")
        void shouldReturnMainWhenBothNull() {
            ProjectConfig config = new ProjectConfig();
            assertThat(config.mainBranch()).isEqualTo("main");
        }
    }

    @Nested
    @DisplayName("setMainBranch()")
    class SetMainBranchTests {

        @Test
        @DisplayName("should update mainBranch and defaultBranch")
        void shouldUpdateBothFields() {
            ProjectConfig config = new ProjectConfig();
            config.setMainBranch("new-main");
            assertThat(config.mainBranch()).isEqualTo("new-main");
            assertThat(config.defaultBranch()).isEqualTo("new-main");
        }

        @Test
        @DisplayName("should sync RAG config branch when set")
        void shouldSyncRagConfigBranch() {
            ProjectConfig config = new ProjectConfig();
            RagConfig ragConfig = new RagConfig(true, "old-branch", null, List.of(), false, 7);
            config.setRagConfig(ragConfig);
            
            config.setMainBranch("new-branch");
            
            // RAG config should be updated with new branch
            assertThat(config.ragConfig().branch()).isEqualTo("new-branch");
        }
    }

    @Nested
    @DisplayName("setDefaultBranch() (deprecated)")
    class SetDefaultBranchTests {

        @Test
        @DisplayName("should set mainBranch when mainBranch is null")
        void shouldSetMainBranchWhenNull() {
            ProjectConfig config = new ProjectConfig();
            config.setDefaultBranch("legacy");
            assertThat(config.mainBranch()).isEqualTo("legacy");
        }

        @Test
        @DisplayName("should not override mainBranch when already set")
        void shouldNotOverrideMainBranch() {
            ProjectConfig config = new ProjectConfig(false, "primary");
            config.setDefaultBranch("secondary");
            assertThat(config.mainBranch()).isEqualTo("primary");
        }
    }

    @Nested
    @DisplayName("isPrAnalysisEnabled()")
    class IsPrAnalysisEnabledTests {

        @Test
        @DisplayName("should return true when prAnalysisEnabled is null")
        void shouldReturnTrueWhenNull() {
            ProjectConfig config = new ProjectConfig();
            config.setPrAnalysisEnabled(null);
            assertThat(config.isPrAnalysisEnabled()).isTrue();
        }

        @Test
        @DisplayName("should return actual value when set")
        void shouldReturnActualValue() {
            ProjectConfig config = new ProjectConfig();
            config.setPrAnalysisEnabled(false);
            assertThat(config.isPrAnalysisEnabled()).isFalse();
        }
    }

    @Nested
    @DisplayName("isBranchAnalysisEnabled()")
    class IsBranchAnalysisEnabledTests {

        @Test
        @DisplayName("should return true when branchAnalysisEnabled is null")
        void shouldReturnTrueWhenNull() {
            ProjectConfig config = new ProjectConfig();
            config.setBranchAnalysisEnabled(null);
            assertThat(config.isBranchAnalysisEnabled()).isTrue();
        }

        @Test
        @DisplayName("should return actual value when set")
        void shouldReturnActualValue() {
            ProjectConfig config = new ProjectConfig();
            config.setBranchAnalysisEnabled(false);
            assertThat(config.isBranchAnalysisEnabled()).isFalse();
        }
    }

    @Nested
    @DisplayName("isCommentCommandsEnabled()")
    class IsCommentCommandsEnabledTests {

        @Test
        @DisplayName("should return false when commentCommands is null")
        void shouldReturnFalseWhenNull() {
            ProjectConfig config = new ProjectConfig();
            assertThat(config.isCommentCommandsEnabled()).isFalse();
        }

        @Test
        @DisplayName("should return enabled status from config")
        void shouldReturnEnabledStatus() {
            ProjectConfig config = new ProjectConfig();
            CommentCommandsConfig commandsConfig = new CommentCommandsConfig(true);
            config.setCommentCommands(commandsConfig);
            assertThat(config.isCommentCommandsEnabled()).isTrue();
        }
    }

    @Nested
    @DisplayName("getCommentCommandsConfig()")
    class GetCommentCommandsConfigTests {

        @Test
        @DisplayName("should return default enabled config when null")
        void shouldReturnDefaultWhenNull() {
            ProjectConfig config = new ProjectConfig();
            CommentCommandsConfig result = config.getCommentCommandsConfig();
            assertThat(result).isNotNull();
            // Default constructor creates enabled config
            assertThat(result.enabled()).isTrue();
        }

        @Test
        @DisplayName("should return actual config when set")
        void shouldReturnActualConfig() {
            ProjectConfig config = new ProjectConfig();
            CommentCommandsConfig commandsConfig = new CommentCommandsConfig(false);
            config.setCommentCommands(commandsConfig);
            assertThat(config.getCommentCommandsConfig()).isSameAs(commandsConfig);
        }
    }

    @Nested
    @DisplayName("ensureMainBranchInPatterns()")
    class EnsureMainBranchInPatternsTests {

        @Test
        @DisplayName("should create branchAnalysis when null")
        void shouldCreateBranchAnalysisWhenNull() {
            ProjectConfig config = new ProjectConfig(false, "main");
            config.setBranchAnalysis(null);
            
            config.ensureMainBranchInPatterns();
            
            assertThat(config.branchAnalysis()).isNotNull();
            assertThat(config.branchAnalysis().prTargetBranches()).contains("main");
            assertThat(config.branchAnalysis().branchPushPatterns()).contains("main");
        }

        @Test
        @DisplayName("should add main branch to existing patterns")
        void shouldAddMainBranchToExistingPatterns() {
            ProjectConfig config = new ProjectConfig(false, "main");
            BranchAnalysisConfig branchConfig = new BranchAnalysisConfig(
                List.of("develop"), List.of("feature/*"));
            config.setBranchAnalysis(branchConfig);
            
            config.ensureMainBranchInPatterns();
            
            assertThat(config.branchAnalysis().prTargetBranches()).containsExactly("main", "develop");
            assertThat(config.branchAnalysis().branchPushPatterns()).containsExactly("main", "feature/*");
        }

        @Test
        @DisplayName("should not duplicate main branch if already present")
        void shouldNotDuplicateMainBranch() {
            ProjectConfig config = new ProjectConfig(false, "main");
            BranchAnalysisConfig branchConfig = new BranchAnalysisConfig(
                List.of("main", "develop"), List.of("main"));
            config.setBranchAnalysis(branchConfig);
            
            config.ensureMainBranchInPatterns();
            
            assertThat(config.branchAnalysis().prTargetBranches()).containsExactly("main", "develop");
        }

        @Test
        @DisplayName("should do nothing when mainBranch is null")
        void shouldDoNothingWhenMainBranchNull() {
            ProjectConfig config = new ProjectConfig();
            config.ensureMainBranchInPatterns();
            // Should not throw
        }
    }

    @Nested
    @DisplayName("setCommentCommandsConfig() (legacy setter)")
    class SetCommentCommandsConfigTests {

        @Test
        @DisplayName("should set commentCommands from legacy field name")
        void shouldSetFromLegacyFieldName() {
            ProjectConfig config = new ProjectConfig();
            CommentCommandsConfig commandsConfig = new CommentCommandsConfig(true);
            config.setCommentCommandsConfig(commandsConfig);
            assertThat(config.commentCommands()).isSameAs(commandsConfig);
        }
    }

    @Nested
    @DisplayName("equals() and hashCode()")
    class EqualsHashCodeTests {

        @Test
        @DisplayName("should be equal to itself")
        void shouldBeEqualToItself() {
            ProjectConfig config = new ProjectConfig(true, "main");
            assertThat(config).isEqualTo(config);
        }

        @Test
        @DisplayName("should be equal to equivalent config")
        void shouldBeEqualToEquivalent() {
            ProjectConfig config1 = new ProjectConfig(true, "main");
            ProjectConfig config2 = new ProjectConfig(true, "main");
            assertThat(config1).isEqualTo(config2);
            assertThat(config1.hashCode()).isEqualTo(config2.hashCode());
        }

        @Test
        @DisplayName("should not be equal to different config")
        void shouldNotBeEqualToDifferent() {
            ProjectConfig config1 = new ProjectConfig(true, "main");
            ProjectConfig config2 = new ProjectConfig(false, "develop");
            assertThat(config1).isNotEqualTo(config2);
        }

        @Test
        @DisplayName("should not be equal to null")
        void shouldNotBeEqualToNull() {
            ProjectConfig config = new ProjectConfig();
            assertThat(config).isNotEqualTo(null);
        }

        @Test
        @DisplayName("should not be equal to different type")
        void shouldNotBeEqualToDifferentType() {
            ProjectConfig config = new ProjectConfig();
            assertThat(config).isNotEqualTo("string");
        }
    }

    @Nested
    @DisplayName("toString()")
    class ToStringTests {

        @Test
        @DisplayName("should include all fields")
        void shouldIncludeAllFields() {
            ProjectConfig config = new ProjectConfig(true, "main");
            String result = config.toString();
            assertThat(result).contains("useLocalMcp=true");
            assertThat(result).contains("mainBranch='main'");
        }
    }

    @Nested
    @DisplayName("Setters")
    class SetterTests {

        @Test
        @DisplayName("should set useLocalMcp")
        void shouldSetUseLocalMcp() {
            ProjectConfig config = new ProjectConfig();
            config.setUseLocalMcp(true);
            assertThat(config.useLocalMcp()).isTrue();
        }

        @Test
        @DisplayName("should set branchAnalysis")
        void shouldSetBranchAnalysis() {
            ProjectConfig config = new ProjectConfig();
            BranchAnalysisConfig branchConfig = new BranchAnalysisConfig(List.of(), List.of());
            config.setBranchAnalysis(branchConfig);
            assertThat(config.branchAnalysis()).isSameAs(branchConfig);
        }

        @Test
        @DisplayName("should set ragConfig")
        void shouldSetRagConfig() {
            ProjectConfig config = new ProjectConfig();
            RagConfig ragConfig = new RagConfig(true, "main", null, List.of(), false, 7);
            config.setRagConfig(ragConfig);
            assertThat(config.ragConfig()).isSameAs(ragConfig);
        }

        @Test
        @DisplayName("should set installationMethod")
        void shouldSetInstallationMethod() {
            ProjectConfig config = new ProjectConfig();
            config.setInstallationMethod(InstallationMethod.GITHUB_ACTION);
            assertThat(config.installationMethod()).isEqualTo(InstallationMethod.GITHUB_ACTION);
        }
    }
}
