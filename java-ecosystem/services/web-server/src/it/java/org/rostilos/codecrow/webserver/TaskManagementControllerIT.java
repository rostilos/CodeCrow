package org.rostilos.codecrow.webserver;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.QaAutoDocConfig;
import org.rostilos.codecrow.core.model.taskmanagement.ETaskManagementConnectionStatus;
import org.rostilos.codecrow.core.model.taskmanagement.ETaskManagementProvider;
import org.rostilos.codecrow.core.model.taskmanagement.TaskManagementConnection;
import org.rostilos.codecrow.core.model.user.User;
import org.rostilos.codecrow.core.model.workspace.EMembershipStatus;
import org.rostilos.codecrow.core.model.workspace.EWorkspaceRole;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.model.workspace.WorkspaceMember;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.core.persistence.repository.taskmanagement.TaskManagementConnectionRepository;
import org.rostilos.codecrow.core.persistence.repository.workspace.WorkspaceMemberRepository;
import org.rostilos.codecrow.core.persistence.repository.workspace.WorkspaceRepository;
import org.rostilos.codecrow.taskmanagement.ETaskManagementPlatform;
import org.rostilos.codecrow.taskmanagement.TaskManagementClient;
import org.rostilos.codecrow.taskmanagement.TaskManagementClientFactory;
import org.rostilos.codecrow.taskmanagement.model.TaskCommentVisibilityOption;
import org.rostilos.codecrow.webserver.auth.service.GoogleOAuthService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.mock.mockito.MockBean;

import java.util.List;
import java.util.Map;

import static org.hamcrest.Matchers.*;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/**
 * Integration tests for task-management endpoints used by project settings.
 */
class TaskManagementControllerIT extends BaseWebServerIT {

    @Autowired
    private WorkspaceRepository workspaceRepository;

    @Autowired
    private WorkspaceMemberRepository workspaceMemberRepository;

    @Autowired
    private ProjectRepository projectRepository;

    @Autowired
    private TaskManagementConnectionRepository connectionRepository;

    @MockBean
    private TaskManagementClientFactory clientFactory;

    @MockBean
    private GoogleOAuthService googleOAuthService;

    private String workspaceSlug;
    private Long projectId;
    private Long connectionId;

    @BeforeEach
    void setUpProjectAndConnection() {
        createTestUser("tmowner", "tmowner@example.com", "password123");

        workspaceSlug = "tm-test-ws";
        User owner = userRepository.findByUsername("tmowner").orElseThrow();
        Workspace workspace = createWorkspace(workspaceSlug, "Task Management Test Workspace", owner);
        projectId = createProject(workspace, "QA Project", "qa-project");
        connectionId = createConnection(workspace, "Jira QA", "https://qa.atlassian.net");
    }

    @Test
    @DisplayName("PUT qa-auto-doc saves Jira group comment visibility")
    void updateQaAutoDocConfigSavesCommentVisibility() {
        authenticatedRequest("tmowner")
                .body("""
                    {
                        "enabled": true,
                        "taskManagementConnectionId": %d,
                        "taskIdPattern": "[A-Z][A-Z0-9]+-\\\\d+",
                        "taskIdSource": "BRANCH_NAME",
                        "templateMode": "BASE",
                        "outputLanguage": "English",
                        "commentVisibility": {
                            "type": "group",
                            "identifier": "group-id-1",
                            "value": "qa-team",
                            "displayName": "QA Team"
                        }
                    }
                    """.formatted(connectionId))
        .when()
                .put("/api/" + workspaceSlug + "/task-management/projects/" + projectId + "/qa-auto-doc")
        .then()
                .statusCode(200)
                .body("commentVisibility.type", equalTo("group"))
                .body("commentVisibility.identifier", equalTo("group-id-1"))
                .body("commentVisibility.value", equalTo("qa-team"));

        Project project = projectRepository.findById(projectId).orElseThrow();
        QaAutoDocConfig.CommentVisibilityConfig visibility =
                project.getConfiguration().qaAutoDoc().commentVisibility();
        org.assertj.core.api.Assertions.assertThat(visibility.displayName()).isEqualTo("QA Team");
        org.assertj.core.api.Assertions.assertThat(project.getConfiguration().qaAutoDoc().outputLanguage())
                .isEqualTo("English");
    }

    @Test
    @DisplayName("PUT qa-auto-doc rejects connection from another workspace")
    void updateQaAutoDocConfigRejectsForeignConnection() {
        User owner = userRepository.findByUsername("tmowner").orElseThrow();
        Workspace foreignWorkspace = createWorkspace("foreign-tm-ws", "Foreign TM Workspace", owner);
        Long foreignConnectionId = createConnection(foreignWorkspace, "Foreign Jira", "https://foreign.atlassian.net");

        authenticatedRequest("tmowner")
                .body("""
                    {
                        "enabled": true,
                        "taskManagementConnectionId": %d,
                        "taskIdPattern": "[A-Z]+-\\\\d+",
                        "taskIdSource": "BRANCH_NAME",
                        "templateMode": "BASE"
                    }
                    """.formatted(foreignConnectionId))
        .when()
                .put("/api/" + workspaceSlug + "/task-management/projects/" + projectId + "/qa-auto-doc")
        .then()
                .statusCode(400)
                .body("error", containsString("not found in workspace"));
    }

    @Test
    @DisplayName("GET comment visibility options returns provider groups")
    void listCommentVisibilityOptionsReturnsProviderGroups() throws Exception {
        TaskManagementClient client = mock(TaskManagementClient.class);
        when(clientFactory.createClient(
                eq(ETaskManagementPlatform.JIRA_CLOUD), anyString(), anyString(), anyString()))
                .thenReturn(client);
        when(client.listCommentVisibilityOptions()).thenReturn(List.of(
                new TaskCommentVisibilityOption("group", "gid-1", "qa-team", "QA Team"),
                new TaskCommentVisibilityOption("group", "gid-2", "developers", "Developers")
        ));

        authenticatedRequest("tmowner")
        .when()
                .get("/api/" + workspaceSlug + "/task-management/connections/"
                     + connectionId + "/comment-visibility-options")
        .then()
                .statusCode(200)
                .body("$", hasSize(2))
                .body("[0].type", equalTo("group"))
                .body("[0].identifier", equalTo("gid-1"))
                .body("[0].displayName", equalTo("QA Team"));
    }

    private Long createConnection(Workspace workspace, String name, String baseUrl) {
        TaskManagementConnection connection = new TaskManagementConnection();
        connection.setWorkspace(workspace);
        connection.setConnectionName(name);
        connection.setProviderType(ETaskManagementProvider.JIRA_CLOUD);
        connection.setStatus(ETaskManagementConnectionStatus.CONNECTED);
        connection.setBaseUrl(baseUrl);
        connection.setCredentials(Map.of("email", "qa@example.com", "apiToken", "token"));
        return connectionRepository.save(connection).getId();
    }

    private Workspace createWorkspace(String slug, String name, User owner) {
        Workspace workspace = workspaceRepository.save(new Workspace(slug, name, null));
        WorkspaceMember member = new WorkspaceMember(
                workspace,
                owner,
                EWorkspaceRole.OWNER,
                EMembershipStatus.ACTIVE
        );
        workspaceMemberRepository.save(member);
        return workspace;
    }

    private Long createProject(Workspace workspace, String name, String namespace) {
        Project project = new Project();
        project.setWorkspace(workspace);
        project.setName(name);
        project.setNamespace(namespace);
        project.setDescription("Project for QA auto-doc tests");
        return projectRepository.save(project).getId();
    }
}
