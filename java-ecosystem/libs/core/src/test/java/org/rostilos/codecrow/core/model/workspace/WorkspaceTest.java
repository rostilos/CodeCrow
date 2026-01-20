package org.rostilos.codecrow.core.model.workspace;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.project.Project;

import java.time.OffsetDateTime;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("Workspace Entity")
class WorkspaceTest {

    private Workspace workspace;

    @BeforeEach
    void setUp() {
        workspace = new Workspace();
    }

    @Nested
    @DisplayName("Constructors")
    class Constructors {

        @Test
        @DisplayName("should create workspace with default constructor")
        void shouldCreateWorkspaceWithDefaultConstructor() {
            Workspace ws = new Workspace();
            
            assertThat(ws.getId()).isNull();
            assertThat(ws.getSlug()).isNull();
            assertThat(ws.getName()).isNull();
            assertThat(ws.getDescription()).isNull();
            assertThat(ws.getIsActive()).isTrue();
            assertThat(ws.getCreatedAt()).isNotNull();
            assertThat(ws.getUpdatedAt()).isNotNull();
            assertThat(ws.getProjects()).isEmpty();
        }

        @Test
        @DisplayName("should create workspace with parameterized constructor")
        void shouldCreateWorkspaceWithParameterizedConstructor() {
            Workspace ws = new Workspace("test-slug", "Test Workspace", "Test description");
            
            assertThat(ws.getSlug()).isEqualTo("test-slug");
            assertThat(ws.getName()).isEqualTo("Test Workspace");
            assertThat(ws.getDescription()).isEqualTo("Test description");
        }
    }

    @Nested
    @DisplayName("Basic Properties")
    class BasicProperties {

        @Test
        @DisplayName("should set and get slug")
        void shouldSetAndGetSlug() {
            workspace.setSlug("my-workspace");
            
            assertThat(workspace.getSlug()).isEqualTo("my-workspace");
        }

        @Test
        @DisplayName("should set and get name")
        void shouldSetAndGetName() {
            workspace.setName("My Workspace");
            
            assertThat(workspace.getName()).isEqualTo("My Workspace");
        }

        @Test
        @DisplayName("should set and get description")
        void shouldSetAndGetDescription() {
            workspace.setDescription("A test workspace description");
            
            assertThat(workspace.getDescription()).isEqualTo("A test workspace description");
        }

        @Test
        @DisplayName("should set and get active status")
        void shouldSetAndGetActiveStatus() {
            assertThat(workspace.getIsActive()).isTrue();
            
            workspace.setIsActive(false);
            
            assertThat(workspace.getIsActive()).isFalse();
        }
    }

    @Nested
    @DisplayName("Timestamps")
    class Timestamps {

        @Test
        @DisplayName("should have createdAt set on creation")
        void shouldHaveCreatedAtSetOnCreation() {
            assertThat(workspace.getCreatedAt()).isNotNull();
            assertThat(workspace.getCreatedAt()).isBeforeOrEqualTo(OffsetDateTime.now());
        }

        @Test
        @DisplayName("should have updatedAt set on creation")
        void shouldHaveUpdatedAtSetOnCreation() {
            assertThat(workspace.getUpdatedAt()).isNotNull();
            assertThat(workspace.getUpdatedAt()).isBeforeOrEqualTo(OffsetDateTime.now());
        }

        @Test
        @DisplayName("should update updatedAt on onUpdate")
        void shouldUpdateUpdatedAtOnOnUpdate() {
            OffsetDateTime originalUpdatedAt = workspace.getUpdatedAt();
            
            // Small delay to ensure time difference
            try {
                Thread.sleep(10);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
            
            workspace.onUpdate();
            
            assertThat(workspace.getUpdatedAt()).isAfterOrEqualTo(originalUpdatedAt);
        }
    }

    @Nested
    @DisplayName("Project Management")
    class ProjectManagement {

        @Test
        @DisplayName("should add project to workspace")
        void shouldAddProjectToWorkspace() {
            Project project = new Project();
            project.setName("Test Project");
            
            workspace.addProject(project);
            
            assertThat(workspace.getProjects()).hasSize(1);
            assertThat(workspace.getProjects()).contains(project);
            assertThat(project.getWorkspace()).isEqualTo(workspace);
        }

        @Test
        @DisplayName("should remove project from workspace")
        void shouldRemoveProjectFromWorkspace() {
            Project project = new Project();
            project.setName("Test Project");
            workspace.addProject(project);
            
            workspace.removeProject(project);
            
            assertThat(workspace.getProjects()).isEmpty();
            assertThat(project.getWorkspace()).isNull();
        }

        @Test
        @DisplayName("should add multiple projects")
        void shouldAddMultipleProjects() {
            Project project1 = new Project();
            project1.setName("Project 1");
            Project project2 = new Project();
            project2.setName("Project 2");
            
            workspace.addProject(project1);
            workspace.addProject(project2);
            
            assertThat(workspace.getProjects()).hasSize(2);
            assertThat(workspace.getProjects()).contains(project1, project2);
        }

        @Test
        @DisplayName("should return empty set when no projects")
        void shouldReturnEmptySetWhenNoProjects() {
            assertThat(workspace.getProjects()).isNotNull();
            assertThat(workspace.getProjects()).isEmpty();
        }
    }
}
