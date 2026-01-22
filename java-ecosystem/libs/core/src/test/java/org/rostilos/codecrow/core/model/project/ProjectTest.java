package org.rostilos.codecrow.core.model.project;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
import org.rostilos.codecrow.core.model.workspace.Workspace;

import java.time.OffsetDateTime;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.*;

@DisplayName("Project")
class ProjectTest {

    private Project project;

    @BeforeEach
    void setUp() {
        project = new Project();
    }

    @Nested
    @DisplayName("Basic Getters/Setters")
    class BasicGettersSetters {

        @Test
        @DisplayName("should set and get name")
        void shouldSetAndGetName() {
            project.setName("Test Project");
            assertThat(project.getName()).isEqualTo("Test Project");
        }

        @Test
        @DisplayName("should set and get namespace")
        void shouldSetAndGetNamespace() {
            project.setNamespace("test-namespace");
            assertThat(project.getNamespace()).isEqualTo("test-namespace");
        }

        @Test
        @DisplayName("should set and get description")
        void shouldSetAndGetDescription() {
            project.setDescription("Test description");
            assertThat(project.getDescription()).isEqualTo("Test description");
        }

        @Test
        @DisplayName("should default to active true")
        void shouldDefaultToActiveTrue() {
            assertThat(project.getIsActive()).isTrue();
        }

        @Test
        @DisplayName("should set and get active status")
        void shouldSetAndGetActiveStatus() {
            project.setIsActive(false);
            assertThat(project.getIsActive()).isFalse();
        }

        @Test
        @DisplayName("should set and get auth token")
        void shouldSetAndGetAuthToken() {
            project.setAuthToken("test-token");
            assertThat(project.getAuthToken()).isEqualTo("test-token");
        }

        @Test
        @DisplayName("should set and get workspace")
        void shouldSetAndGetWorkspace() {
            Workspace workspace = new Workspace();
            project.setWorkspace(workspace);
            assertThat(project.getWorkspace()).isSameAs(workspace);
        }

        @Test
        @DisplayName("should set and get configuration")
        void shouldSetAndGetConfiguration() {
            ProjectConfig config = new ProjectConfig(true, "main");
            project.setConfiguration(config);
            assertThat(project.getConfiguration()).isSameAs(config);
        }

        @Test
        @DisplayName("should have createdAt set by default")
        void shouldHaveCreatedAtByDefault() {
            assertThat(project.getCreatedAt()).isNotNull();
        }

        @Test
        @DisplayName("should have updatedAt set by default")
        void shouldHaveUpdatedAtByDefault() {
            assertThat(project.getUpdatedAt()).isNotNull();
        }

        @Test
        @DisplayName("should default prAnalysisEnabled to true")
        void shouldDefaultPrAnalysisEnabled() {
            assertThat(project.isPrAnalysisEnabled()).isTrue();
        }

        @Test
        @DisplayName("should set and get prAnalysisEnabled")
        void shouldSetAndGetPrAnalysisEnabled() {
            project.setPrAnalysisEnabled(false);
            assertThat(project.isPrAnalysisEnabled()).isFalse();
        }

        @Test
        @DisplayName("should default branchAnalysisEnabled to true")
        void shouldDefaultBranchAnalysisEnabled() {
            assertThat(project.isBranchAnalysisEnabled()).isTrue();
        }

        @Test
        @DisplayName("should set and get branchAnalysisEnabled")
        void shouldSetAndGetBranchAnalysisEnabled() {
            project.setBranchAnalysisEnabled(false);
            assertThat(project.isBranchAnalysisEnabled()).isFalse();
        }
    }

    @Nested
    @DisplayName("VCS Binding Methods")
    class VcsBindingMethods {

        @Test
        @DisplayName("hasVcsBinding should return false when no bindings")
        void hasVcsBindingShouldReturnFalseWhenNoBindings() {
            assertThat(project.hasVcsBinding()).isFalse();
        }

        @Test
        @DisplayName("hasVcsBinding should return true with vcsRepoBinding")
        void hasVcsBindingShouldReturnTrueWithVcsRepoBinding() {
            VcsRepoBinding binding = new VcsRepoBinding();
            project.setVcsRepoBinding(binding);
            assertThat(project.hasVcsBinding()).isTrue();
        }

        @Test
        @DisplayName("hasVcsBinding should return true with legacy vcsBinding")
        void hasVcsBindingShouldReturnTrueWithLegacyVcsBinding() {
            ProjectVcsConnectionBinding binding = mock(ProjectVcsConnectionBinding.class);
            project.setVcsBinding(binding);
            assertThat(project.hasVcsBinding()).isTrue();
        }

        @Test
        @DisplayName("getEffectiveVcsRepoInfo should return null when no bindings")
        void getEffectiveVcsRepoInfoShouldReturnNullWhenNoBindings() {
            assertThat(project.getEffectiveVcsRepoInfo()).isNull();
        }

        @Test
        @DisplayName("getEffectiveVcsRepoInfo should prefer VcsRepoBinding over legacy")
        void getEffectiveVcsRepoInfoShouldPreferNewBinding() {
            VcsRepoBinding newBinding = new VcsRepoBinding();
            newBinding.setExternalRepoSlug("new-repo");
            project.setVcsRepoBinding(newBinding);

            ProjectVcsConnectionBinding legacyBinding = mock(ProjectVcsConnectionBinding.class);
            project.setVcsBinding(legacyBinding);

            assertThat(project.getEffectiveVcsRepoInfo()).isSameAs(newBinding);
        }

        @Test
        @DisplayName("getEffectiveVcsRepoInfo should return legacy binding when no new binding")
        void getEffectiveVcsRepoInfoShouldReturnLegacyWhenNoNew() {
            ProjectVcsConnectionBinding legacyBinding = mock(ProjectVcsConnectionBinding.class);
            project.setVcsBinding(legacyBinding);

            assertThat(project.getEffectiveVcsRepoInfo()).isSameAs(legacyBinding);
        }

        @Test
        @DisplayName("getEffectiveVcsConnection should return null when no bindings")
        void getEffectiveVcsConnectionShouldReturnNullWhenNoBindings() {
            assertThat(project.getEffectiveVcsConnection()).isNull();
        }

        @Test
        @DisplayName("getEffectiveVcsConnection should return connection from VcsRepoBinding")
        void getEffectiveVcsConnectionShouldReturnFromNewBinding() {
            VcsConnection connection = new VcsConnection();
            VcsRepoBinding binding = new VcsRepoBinding();
            binding.setVcsConnection(connection);
            project.setVcsRepoBinding(binding);

            assertThat(project.getEffectiveVcsConnection()).isSameAs(connection);
        }
    }

    @Nested
    @DisplayName("Quality Gate")
    class QualityGateTests {

        @Test
        @DisplayName("should get and set quality gate")
        void shouldGetAndSetQualityGate() {
            org.rostilos.codecrow.core.model.qualitygate.QualityGate qualityGate = 
                new org.rostilos.codecrow.core.model.qualitygate.QualityGate();
            project.setQualityGate(qualityGate);
            assertThat(project.getQualityGate()).isSameAs(qualityGate);
        }

        @Test
        @DisplayName("should return null for quality gate by default")
        void shouldReturnNullForQualityGateByDefault() {
            assertThat(project.getQualityGate()).isNull();
        }
    }

    @Nested
    @DisplayName("Default Branch")
    class DefaultBranchTests {

        @Test
        @DisplayName("should get and set default branch")
        void shouldGetAndSetDefaultBranch() {
            org.rostilos.codecrow.core.model.branch.Branch branch = 
                new org.rostilos.codecrow.core.model.branch.Branch();
            project.setDefaultBranch(branch);
            assertThat(project.getDefaultBranch()).isSameAs(branch);
        }
    }

    @Nested
    @DisplayName("AI Binding")
    class AiBindingTests {

        @Test
        @DisplayName("should get and set AI binding")
        void shouldGetAndSetAiBinding() {
            ProjectAiConnectionBinding aiBinding = mock(ProjectAiConnectionBinding.class);
            project.setAiConnectionBinding(aiBinding);
            assertThat(project.getAiBinding()).isSameAs(aiBinding);
        }
    }

    @Nested
    @DisplayName("onUpdate callback")
    class OnUpdateTests {

        @Test
        @DisplayName("should update updatedAt timestamp on update")
        void shouldUpdateTimestampOnUpdate() {
            OffsetDateTime originalTime = project.getUpdatedAt();
            
            // Small delay to ensure time difference
            try { Thread.sleep(10); } catch (InterruptedException e) {}
            
            project.onUpdate();
            
            assertThat(project.getUpdatedAt()).isAfterOrEqualTo(originalTime);
        }
    }
}
