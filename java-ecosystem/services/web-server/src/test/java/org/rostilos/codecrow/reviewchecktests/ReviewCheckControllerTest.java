package org.rostilos.codecrow.reviewchecktests;

import org.rostilos.codecrow.webserver.reviewcheck.ReviewCheckController;
import org.rostilos.codecrow.webserver.reviewcheck.ReviewCheckManagementService;

import org.junit.jupiter.api.*;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.security.web.WorkspaceSecurity;
import org.rostilos.codecrow.webserver.project.service.ProjectService;
import org.rostilos.codecrow.webserver.workspace.service.WorkspaceService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.context.annotation.Bean;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.config.annotation.method.configuration.EnableMethodSecurity;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.test.context.junit.jupiter.SpringJUnitConfig;
import java.util.List;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

@SpringJUnitConfig(ReviewCheckControllerTest.Config.class)
class ReviewCheckControllerTest {
    @TestConfiguration @EnableMethodSecurity
    static class Config {
        @Bean ReviewCheckController controller(ReviewCheckManagementService checks, WorkspaceService workspaces, ProjectService projects) {
            return new ReviewCheckController(checks, workspaces, projects);
        }
    }
    @Autowired ReviewCheckController controller;
    @MockBean ReviewCheckManagementService checks;
    @MockBean(name = "workspaceSecurity") WorkspaceSecurity security;
    @MockBean WorkspaceService workspaces;
    @MockBean ProjectService projects;

    @BeforeEach void authenticate() {
        reset(checks, security, workspaces, projects);
        SecurityContextHolder.getContext().setAuthentication(new UsernamePasswordAuthenticationToken("member", "", List.of()));
        var workspace = mock(Workspace.class); when(workspace.getId()).thenReturn(10L);
        var project = mock(Project.class); when(project.getId()).thenReturn(20L);
        when(workspaces.getWorkspaceBySlug("owner")).thenReturn(workspace);
        when(projects.getProjectByWorkspaceAndNamespace(10L, "repo")).thenReturn(project);
    }
    @AfterEach void clear() { SecurityContextHolder.clearContext(); }

    @Test void membershipScopesReadsAndReviewerPermissionScopesWrites() {
        when(security.isWorkspaceMember(eq("owner"), any())).thenReturn(true);
        controller.list("owner", "repo", 42L, 3);
        verify(checks).get(20L, 42L, 3);
        var update = new ReviewCheckController.StatusUpdate(true, "Accepted risk");
        assertThatThrownBy(() -> controller.status("owner", "repo", 7L, update)).isInstanceOf(AccessDeniedException.class);
        verify(checks, never()).updateStatus(any(), any(), anyBoolean(), any());
        when(security.isWorkspaceReviewer(eq("owner"), any())).thenReturn(true);
        controller.status("owner", "repo", 7L, update);
        verify(checks).updateStatus(20L, 7L, true, "Accepted risk");
    }
    @Test void anotherWorkspaceCannotReadOrWrite() {
        assertThatThrownBy(() -> controller.list("foreign", "repo", 42L, 0)).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> controller.status("foreign", "repo", 7L, new ReviewCheckController.StatusUpdate(true, null)))
                .isInstanceOf(AccessDeniedException.class);
        verifyNoInteractions(checks, workspaces, projects);
    }
}
