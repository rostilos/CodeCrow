package org.rostilos.codecrow.pipelineagent.generic.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.project.config.TaskManagementConfig;
import org.rostilos.codecrow.core.model.taskmanagement.ETaskManagementConnectionStatus;
import org.rostilos.codecrow.core.model.taskmanagement.ETaskManagementProvider;
import org.rostilos.codecrow.core.model.taskmanagement.TaskManagementConnection;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.taskmanagement.TaskManagementConnectionRepository;
import org.rostilos.codecrow.taskmanagement.ETaskManagementPlatform;
import org.rostilos.codecrow.taskmanagement.TaskManagementClient;
import org.rostilos.codecrow.taskmanagement.TaskManagementClientFactory;
import org.rostilos.codecrow.taskmanagement.model.TaskDetails;
import org.springframework.test.util.ReflectionTestUtils;

import java.time.OffsetDateTime;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
@DisplayName("TaskContextEnrichmentService")
class TaskContextEnrichmentServiceTest {

    @Mock
    private TaskManagementConnectionRepository connectionRepository;

    @Mock
    private TaskManagementClientFactory clientFactory;

    @Mock
    private TaskManagementClient taskManagementClient;

    private TaskContextEnrichmentService service;

    @BeforeEach
    void setUp() {
        service = new TaskContextEnrichmentService(connectionRepository, clientFactory);
    }

    @Test
    @DisplayName("should skip when project disables task context analysis")
    void shouldSkipWhenProjectDisablesTaskContextAnalysis() {
        Project project = projectWithWorkspace(10L);
        ProjectConfig config = new ProjectConfig();
        config.setTaskContextAnalysisEnabled(false);
        project.setConfiguration(config);

        Map<String, String> context = service.resolveTaskContext(
                project,
                "feature/PROJ-123-customer-export",
                "Add export",
                "");

        assertThat(context).isEmpty();
        verifyNoInteractions(connectionRepository, clientFactory);
    }

    @Test
    @DisplayName("should resolve task key without fetching task details")
    void shouldResolveTaskKeyWithoutFetchingTaskDetails() {
        Project project = projectWithWorkspaceAndTaskConfig(10L, 1L);

        Optional<String> taskKey = service.resolveTaskKey(
                project,
                "feature/PROJ-123-customer-export",
                "Add export",
                "");

        assertThat(taskKey).contains("PROJ-123");
        verifyNoInteractions(connectionRepository, clientFactory);
    }

    @Test
    @DisplayName("should not resolve task key when project disables task context analysis")
    void shouldNotResolveTaskKeyWhenProjectDisablesTaskContextAnalysis() {
        Project project = projectWithWorkspaceAndTaskConfig(10L, 1L);
        ProjectConfig config = project.getConfiguration();
        config.setTaskContextAnalysisEnabled(false);

        Optional<String> taskKey = service.resolveTaskKey(
                project,
                "feature/PROJ-123-customer-export",
                "Add export",
                "");

        assertThat(taskKey).isEmpty();
        verifyNoInteractions(connectionRepository, clientFactory);
    }

    @Test
    @DisplayName("should fetch task context using project-bound connection and branch task key")
    void shouldFetchTaskContextFromBranchTaskKey() throws Exception {
        Project project = projectWithWorkspaceAndTaskConfig(10L, 1L);
        TaskManagementConnection connection = connection(1L);

        when(connectionRepository.findByIdAndWorkspaceId(1L, 10L)).thenReturn(java.util.Optional.of(connection));
        when(clientFactory.createClient(
                eq(ETaskManagementPlatform.JIRA_CLOUD),
                eq("https://jira.example"),
                eq("qa@example.com"),
                eq("token")))
                .thenReturn(taskManagementClient);
        when(taskManagementClient.getTaskDetails("PROJ-123")).thenReturn(new TaskDetails(
                "PROJ-123",
                "Add customer export",
                "Implement CSV export\n\nAcceptance Criteria\n- Exports active customers",
                "In Progress",
                "Dev A",
                "PM B",
                "High",
                "Story",
                OffsetDateTime.parse("2026-01-01T00:00:00Z"),
                OffsetDateTime.parse("2026-01-02T00:00:00Z"),
                "https://jira.example/browse/PROJ-123"
        ));

        Map<String, String> context = service.resolveTaskContext(
                project,
                "feature/PROJ-123-customer-export",
                "Add export",
                "");

        assertThat(context).containsEntry("task_key", "PROJ-123");
        assertThat(context).containsEntry("task_summary", "Add customer export");
        assertThat(context).containsEntry("task_type", "Story");
        assertThat(context).containsEntry("priority", "High");
        assertThat(context).containsEntry("assignee", "Dev A");
        assertThat(context).containsEntry("web_url", "https://jira.example/browse/PROJ-123");
        assertThat(context).containsEntry("provider", "jira-cloud");
    }

    @Test
    @DisplayName("should skip when no task-management connection is bound to project")
    void shouldSkipWhenNoConnectionIsBoundToProject() {
        Project project = projectWithWorkspace(10L);

        Map<String, String> context = service.resolveTaskContext(
                project,
                "feature/PROJ-123-customer-export",
                "Add export",
                "");

        assertThat(context).isEmpty();
        verifyNoInteractions(connectionRepository);
        verifyNoInteractions(clientFactory);
    }

    @Test
    @DisplayName("should skip configured connection that is not connected")
    void shouldSkipConfiguredConnectionThatIsNotConnected() {
        Project project = projectWithWorkspaceAndTaskConfig(10L, 1L);
        TaskManagementConnection connection = connection(1L);
        connection.setStatus(ETaskManagementConnectionStatus.ERROR);

        when(connectionRepository.findByIdAndWorkspaceId(1L, 10L)).thenReturn(java.util.Optional.of(connection));

        Map<String, String> context = service.resolveTaskContext(
                project,
                "feature/PROJ-123-customer-export",
                "Add export",
                "");

        assertThat(context).isEmpty();
        verifyNoInteractions(clientFactory);
    }

    private Project projectWithWorkspace(Long workspaceId) {
        Workspace workspace = new Workspace("ws", "Workspace", null);
        ReflectionTestUtils.setField(workspace, "id", workspaceId);
        Project project = new Project();
        ReflectionTestUtils.setField(project, "id", 99L);
        project.setWorkspace(workspace);
        return project;
    }

    private Project projectWithWorkspaceAndTaskConfig(Long workspaceId, Long connectionId) {
        Project project = projectWithWorkspace(workspaceId);
        ProjectConfig config = new ProjectConfig();
        config.setTaskManagement(new TaskManagementConfig(
                connectionId,
                TaskManagementConfig.DEFAULT_TASK_ID_PATTERN,
                TaskManagementConfig.TaskIdSource.BRANCH_NAME
        ));
        project.setConfiguration(config);
        return project;
    }

    private TaskManagementConnection connection(Long id) {
        TaskManagementConnection connection = new TaskManagementConnection();
        connection.setId(id);
        connection.setProviderType(ETaskManagementProvider.JIRA_CLOUD);
        connection.setStatus(ETaskManagementConnectionStatus.CONNECTED);
        connection.setBaseUrl("https://jira.example");
        connection.setCredentials(Map.of("email", "qa@example.com", "apiToken", "token"));
        return connection;
    }
}
