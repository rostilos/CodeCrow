package org.rostilos.codecrow.core.model.project;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.user.User;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("ProjectMember")
class ProjectMemberTest {

    private ProjectMember projectMember;

    @BeforeEach
    void setUp() {
        projectMember = new ProjectMember();
    }

    @Nested
    @DisplayName("Default values")
    class DefaultValues {

        @Test
        @DisplayName("should have VIEWER role by default")
        void shouldHaveViewerRoleByDefault() {
            assertThat(projectMember.getRole()).isEqualTo(EProjectRole.VIEWER);
        }

        @Test
        @DisplayName("should have null id by default")
        void shouldHaveNullIdByDefault() {
            assertThat(projectMember.getId()).isNull();
        }

        @Test
        @DisplayName("should have null project by default")
        void shouldHaveNullProjectByDefault() {
            assertThat(projectMember.getProject()).isNull();
        }

        @Test
        @DisplayName("should have null user by default")
        void shouldHaveNullUserByDefault() {
            assertThat(projectMember.getUser()).isNull();
        }
    }

    @Nested
    @DisplayName("Project association")
    class ProjectAssociation {

        @Test
        @DisplayName("should set and get project")
        void shouldSetAndGetProject() {
            Project project = new Project();
            project.setName("Test Project");
            
            projectMember.setProject(project);
            
            assertThat(projectMember.getProject()).isEqualTo(project);
            assertThat(projectMember.getProject().getName()).isEqualTo("Test Project");
        }
    }

    @Nested
    @DisplayName("User association")
    class UserAssociation {

        @Test
        @DisplayName("should set and get user")
        void shouldSetAndGetUser() {
            User user = new User();
            user.setEmail("test@example.com");
            
            projectMember.setUser(user);
            
            assertThat(projectMember.getUser()).isEqualTo(user);
            assertThat(projectMember.getUser().getEmail()).isEqualTo("test@example.com");
        }
    }

    @Nested
    @DisplayName("Role management")
    class RoleManagement {

        @Test
        @DisplayName("should set and get role")
        void shouldSetAndGetRole() {
            projectMember.setRole(EProjectRole.OWNER);
            assertThat(projectMember.getRole()).isEqualTo(EProjectRole.OWNER);
        }

        @Test
        @DisplayName("should allow all project roles")
        void shouldAllowAllProjectRoles() {
            for (EProjectRole role : EProjectRole.values()) {
                projectMember.setRole(role);
                assertThat(projectMember.getRole()).isEqualTo(role);
            }
        }
    }
}
