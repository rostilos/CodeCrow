package org.rostilos.codecrow.pipelineagent.generic.processor.command;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.project.config.QaAutoDocConfig;
import org.rostilos.codecrow.core.model.taskmanagement.ETaskManagementProvider;
import org.rostilos.codecrow.core.model.taskmanagement.TaskManagementConnection;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.persistence.repository.taskmanagement.TaskManagementConnectionRepository;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.rostilos.codecrow.pipelineagent.generic.dto.webhook.WebhookPayload;
import org.rostilos.codecrow.pipelineagent.generic.webhookhandler.WebhookHandler.WebhookResult;
import org.rostilos.codecrow.pipelineagent.qadoc.QaDocGenerationService;
import org.rostilos.codecrow.taskmanagement.ETaskManagementPlatform;
import org.rostilos.codecrow.taskmanagement.TaskManagementClient;
import org.rostilos.codecrow.taskmanagement.TaskManagementClientFactory;
import org.rostilos.codecrow.taskmanagement.model.TaskComment;
import org.rostilos.codecrow.taskmanagement.model.TaskDetails;
import org.springframework.test.util.ReflectionTestUtils;

import java.io.IOException;
import java.time.OffsetDateTime;
import java.util.*;
import java.util.function.Consumer;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
@DisplayName("QaDocCommandProcessor")
class QaDocCommandProcessorTest {

    @Mock private TaskManagementConnectionRepository connectionRepository;
    @Mock private TaskManagementClientFactory clientFactory;
    @Mock private QaDocGenerationService qaDocGenerationService;
    @Mock private CodeAnalysisService codeAnalysisService;
    @Mock private TaskManagementClient taskManagementClient;
    @Mock private VcsClientProvider vcsClientProvider;
    @Mock private VcsServiceFactory vcsServiceFactory;

    private QaDocCommandProcessor processor;
    private Project project;
    private final ObjectMapper mapper = new ObjectMapper();
    private Consumer<Map<String, Object>> eventConsumer;
    private List<Map<String, Object>> capturedEvents;

    // ── Shared fixtures ──

    private static final Long PROJECT_ID = 42L;
    private static final Long CONNECTION_ID = 100L;
    private static final String TASK_ID = "PROJ-123";
    private static final String PR_ID = "7";

    @BeforeEach
    void setUp() {
        processor = new QaDocCommandProcessor(
                connectionRepository,
                clientFactory,
                qaDocGenerationService,
                codeAnalysisService,
                vcsClientProvider,
                vcsServiceFactory
        );

        project = new Project();
        ReflectionTestUtils.setField(project, "id", PROJECT_ID);
        ReflectionTestUtils.setField(project, "name", "Test Project");

        capturedEvents = new ArrayList<>();
        eventConsumer = capturedEvents::add;
    }

    // ── Helpers ──

    private QaAutoDocConfig enabledConfig() {
        return new QaAutoDocConfig(
                true, CONNECTION_ID, "[A-Z][A-Z0-9]+-\\d+",
                QaAutoDocConfig.TaskIdSource.BRANCH_NAME,
                QaAutoDocConfig.TemplateMode.BASE, null
        );
    }

    private void configureProject(QaAutoDocConfig qaConfig) {
        ProjectConfig config = new ProjectConfig();
        config.setQaAutoDoc(qaConfig);
        ReflectionTestUtils.setField(project, "configuration", config);
    }

    private WebhookPayload createPayload(String sourceBranch) {
        return new WebhookPayload(
                EVcsProvider.GITHUB, "issue_comment", "repo-id", "my-repo", "my-org",
                PR_ID, sourceBranch, "main", "abc123", null
        );
    }

    private WebhookPayload createPayloadWithRawPr(String prTitle) {
        ObjectNode root = mapper.createObjectNode();
        ObjectNode pr = root.putObject("pull_request");
        pr.put("title", prTitle);
        pr.put("body", "Description of the PR");

        return new WebhookPayload(
                EVcsProvider.GITHUB, "issue_comment", "repo-id", "my-repo", "my-org",
                PR_ID, "feature/PROJ-999", "main", "abc123", root
        );
    }

    private TaskManagementConnection createConnection() {
        TaskManagementConnection conn = new TaskManagementConnection();
        ReflectionTestUtils.setField(conn, "id", CONNECTION_ID);
        conn.setProviderType(ETaskManagementProvider.JIRA_CLOUD);
        conn.setBaseUrl("https://test.atlassian.net");
        conn.setCredentials(Map.of("email", "user@test.com", "apiToken", "token123"));
        return conn;
    }

    private TaskDetails sampleTaskDetails() {
        return new TaskDetails(
                TASK_ID, "Implement feature X", "Full description",
                "In Progress", "dev@test.com", "pm@test.com",
                "High", "Story", OffsetDateTime.now(), OffsetDateTime.now(),
                "https://test.atlassian.net/browse/PROJ-123"
        );
    }

    private CodeAnalysis createAnalysisWithIssues(int totalIssues, String... filePaths) {
        CodeAnalysis analysis = new CodeAnalysis();
        ReflectionTestUtils.setField(analysis, "totalIssues", totalIssues);
        List<CodeAnalysisIssue> issues = new ArrayList<>();
        for (String path : filePaths) {
            CodeAnalysisIssue issue = new CodeAnalysisIssue();
            ReflectionTestUtils.setField(issue, "filePath", path);
            issues.add(issue);
        }
        ReflectionTestUtils.setField(analysis, "issues", issues);
        return analysis;
    }

    private void setupHappyPath() throws IOException {
        configureProject(enabledConfig());

        TaskManagementConnection conn = createConnection();
        when(connectionRepository.findById(CONNECTION_ID)).thenReturn(Optional.of(conn));
        when(clientFactory.createClient(
                eq(ETaskManagementPlatform.JIRA_CLOUD), anyString(), anyString(), anyString()
        )).thenReturn(taskManagementClient);

        TaskDetails td = sampleTaskDetails();
        when(taskManagementClient.getTaskDetails(TASK_ID)).thenReturn(td);
        lenient().when(taskManagementClient.findCommentByMarker(eq(TASK_ID), anyString()))
                .thenReturn(Optional.empty());
        lenient().when(taskManagementClient.postComment(eq(TASK_ID), anyString()))
                .thenReturn(new TaskComment("c-1", "bot", "doc", OffsetDateTime.now(), null));

        CodeAnalysis analysis = createAnalysisWithIssues(5, "src/A.java", "src/B.java", "src/A.java");
        when(codeAnalysisService.getPreviousVersionCodeAnalysis(PROJECT_ID, 7L))
                .thenReturn(Optional.of(analysis));

        when(qaDocGenerationService.generateQaDocumentation(
                any(Project.class), anyLong(), anyInt(), anyInt(),
                anyMap(), any(QaAutoDocConfig.class), any(TaskDetails.class), any(), any()
        )).thenReturn("## QA Documentation\n\nTest steps here...");
    }

    // ════════════════════════════════════════════════════════════════
    // Tests
    // ════════════════════════════════════════════════════════════════

    @Nested
    @DisplayName("process() — configuration validation")
    class ConfigValidation {

        @Test
        @DisplayName("should return error when project has no config")
        void shouldReturnErrorWhenNoConfig() {
            ReflectionTestUtils.setField(project, "configuration", null);
            WebhookPayload payload = createPayload("feature/PROJ-123");

            WebhookResult result = processor.process(payload, project, eventConsumer, Map.of());

            assertThat(result.success()).isFalse();
            assertThat(result.message()).contains("not enabled");
        }

        @Test
        @DisplayName("should return error when QA auto-doc is disabled")
        void shouldReturnErrorWhenDisabled() {
            configureProject(new QaAutoDocConfig()); // defaults: disabled
            WebhookPayload payload = createPayload("feature/PROJ-123");

            WebhookResult result = processor.process(payload, project, eventConsumer, Map.of());

            assertThat(result.success()).isFalse();
            assertThat(result.message()).contains("not enabled");
        }

        @Test
        @DisplayName("should return error when QA config is not fully configured")
        void shouldReturnErrorWhenNotFullyConfigured() {
            // enabled=true but no connection ID → not fully configured
            configureProject(new QaAutoDocConfig(true, null, "[A-Z]+-\\d+",
                    QaAutoDocConfig.TaskIdSource.BRANCH_NAME, QaAutoDocConfig.TemplateMode.BASE, null));
            WebhookPayload payload = createPayload("feature/PROJ-123");

            WebhookResult result = processor.process(payload, project, eventConsumer, Map.of());

            assertThat(result.success()).isFalse();
            assertThat(result.message()).contains("not enabled");
        }
    }

    @Nested
    @DisplayName("process() — connection resolution")
    class ConnectionResolution {

        @Test
        @DisplayName("should return error when connection not found")
        void shouldReturnErrorWhenConnectionNotFound() {
            configureProject(enabledConfig());
            when(connectionRepository.findById(CONNECTION_ID)).thenReturn(Optional.empty());
            WebhookPayload payload = createPayload("feature/PROJ-123");

            WebhookResult result = processor.process(payload, project, eventConsumer, Map.of());

            assertThat(result.success()).isFalse();
            assertThat(result.message()).contains("connection not found");
        }
    }

    @Nested
    @DisplayName("process() — task ID extraction")
    class TaskIdExtraction {

        @BeforeEach
        void setUpConnection() {
            configureProject(enabledConfig());
            TaskManagementConnection conn = createConnection();
            when(connectionRepository.findById(CONNECTION_ID)).thenReturn(Optional.of(conn));
            when(clientFactory.createClient(any(), anyString(), anyString(), anyString()))
                    .thenReturn(taskManagementClient);
        }

        @Test
        @DisplayName("should use explicit task ID from additionalData")
        void shouldUseExplicitTaskId() throws IOException {
            when(taskManagementClient.getTaskDetails("EXPLICIT-999")).thenReturn(sampleTaskDetails());
            when(taskManagementClient.findCommentByMarker(eq("EXPLICIT-999"), anyString()))
                    .thenReturn(Optional.empty());
            when(taskManagementClient.postComment(eq("EXPLICIT-999"), anyString()))
                    .thenReturn(new TaskComment("c-1", "bot", "doc", OffsetDateTime.now(), null));
            when(qaDocGenerationService.generateQaDocumentation(
                    any(), anyLong(), anyInt(), anyInt(), anyMap(), any(), any(), any(), any()
            )).thenReturn("doc content");

            WebhookPayload payload = createPayload("feature/OTHER-456");

            WebhookResult result = processor.process(
                    payload, project, eventConsumer, Map.of("taskId", "EXPLICIT-999"));

            assertThat(result.success()).isTrue();
            verify(taskManagementClient).getTaskDetails("EXPLICIT-999");
        }

        @Test
        @DisplayName("should extract task ID from branch name by default")
        void shouldExtractFromBranchName() throws IOException {
            when(taskManagementClient.getTaskDetails("PROJ-123")).thenReturn(sampleTaskDetails());
            when(taskManagementClient.findCommentByMarker(eq("PROJ-123"), anyString()))
                    .thenReturn(Optional.empty());
            when(taskManagementClient.postComment(eq("PROJ-123"), anyString()))
                    .thenReturn(new TaskComment("c-1", "bot", "doc", OffsetDateTime.now(), null));
            when(qaDocGenerationService.generateQaDocumentation(
                    any(), anyLong(), anyInt(), anyInt(), anyMap(), any(), any(), any(), any()
            )).thenReturn("doc content");

            WebhookPayload payload = createPayload("feature/PROJ-123-add-login");

            WebhookResult result = processor.process(payload, project, eventConsumer, Map.of());

            assertThat(result.success()).isTrue();
            verify(taskManagementClient).getTaskDetails("PROJ-123");
        }

        @Test
        @DisplayName("should extract task ID from PR title when configured")
        void shouldExtractFromPrTitle() throws IOException {
            QaAutoDocConfig prTitleConfig = new QaAutoDocConfig(
                    true, CONNECTION_ID, "[A-Z][A-Z0-9]+-\\d+",
                    QaAutoDocConfig.TaskIdSource.PR_TITLE,
                    QaAutoDocConfig.TemplateMode.BASE, null
            );
            configureProject(prTitleConfig);

            when(taskManagementClient.getTaskDetails("FEAT-42")).thenReturn(sampleTaskDetails());
            when(taskManagementClient.findCommentByMarker(eq("FEAT-42"), anyString()))
                    .thenReturn(Optional.empty());
            when(taskManagementClient.postComment(eq("FEAT-42"), anyString()))
                    .thenReturn(new TaskComment("c-1", "bot", "doc", OffsetDateTime.now(), null));
            when(qaDocGenerationService.generateQaDocumentation(
                    any(), anyLong(), anyInt(), anyInt(), anyMap(), any(), any(), any(), any()
            )).thenReturn("doc content");

            ObjectNode root = mapper.createObjectNode();
            ObjectNode pr = root.putObject("pullrequest"); // Bitbucket format
            pr.put("title", "FEAT-42: Add login feature");

            WebhookPayload payload = new WebhookPayload(
                    EVcsProvider.BITBUCKET_CLOUD, "pullrequest:comment_created",
                    "repo-id", "my-repo", "my-org",
                    PR_ID, "feature/other", "main", "abc123", root
            );

            WebhookResult result = processor.process(payload, project, eventConsumer, Map.of());

            assertThat(result.success()).isTrue();
            verify(taskManagementClient).getTaskDetails("FEAT-42");
        }

        @Test
        @DisplayName("should extract task ID from GitHub PR title when configured")
        void shouldExtractFromGitHubPrTitle() throws IOException {
            QaAutoDocConfig prTitleConfig = new QaAutoDocConfig(
                    true, CONNECTION_ID, "[A-Z][A-Z0-9]+-\\d+",
                    QaAutoDocConfig.TaskIdSource.PR_TITLE,
                    QaAutoDocConfig.TemplateMode.BASE, null
            );
            configureProject(prTitleConfig);

            when(taskManagementClient.getTaskDetails("GH-77")).thenReturn(sampleTaskDetails());
            when(taskManagementClient.findCommentByMarker(eq("GH-77"), anyString()))
                    .thenReturn(Optional.empty());
            when(taskManagementClient.postComment(eq("GH-77"), anyString()))
                    .thenReturn(new TaskComment("c-1", "bot", "doc", OffsetDateTime.now(), null));
            when(qaDocGenerationService.generateQaDocumentation(
                    any(), anyLong(), anyInt(), anyInt(), anyMap(), any(), any(), any(), any()
            )).thenReturn("doc content");

            ObjectNode root = mapper.createObjectNode();
            ObjectNode pr = root.putObject("pull_request"); // GitHub format
            pr.put("title", "GH-77: Implement OAuth flow");
            pr.put("body", "Adds OAuth2 support");

            WebhookPayload payload = new WebhookPayload(
                    EVcsProvider.GITHUB, "issue_comment",
                    "repo-id", "my-repo", "my-org",
                    PR_ID, "feature/other", "main", "abc123", root
            );

            WebhookResult result = processor.process(payload, project, eventConsumer, Map.of());

            assertThat(result.success()).isTrue();
            verify(taskManagementClient).getTaskDetails("GH-77");
        }

        @Test
        @DisplayName("should extract task ID from GitHub PR description when configured")
        void shouldExtractFromGitHubPrDescription() throws IOException {
            QaAutoDocConfig prDescConfig = new QaAutoDocConfig(
                    true, CONNECTION_ID, "[A-Z][A-Z0-9]+-\\d+",
                    QaAutoDocConfig.TaskIdSource.PR_DESCRIPTION,
                    QaAutoDocConfig.TemplateMode.BASE, null
            );
            configureProject(prDescConfig);

            when(taskManagementClient.getTaskDetails("BACK-55")).thenReturn(sampleTaskDetails());
            when(taskManagementClient.findCommentByMarker(eq("BACK-55"), anyString()))
                    .thenReturn(Optional.empty());
            when(taskManagementClient.postComment(eq("BACK-55"), anyString()))
                    .thenReturn(new TaskComment("c-1", "bot", "doc", OffsetDateTime.now(), null));
            when(qaDocGenerationService.generateQaDocumentation(
                    any(), anyLong(), anyInt(), anyInt(), anyMap(), any(), any(), any(), any()
            )).thenReturn("doc content");

            ObjectNode root = mapper.createObjectNode();
            ObjectNode pr = root.putObject("pull_request"); // GitHub format
            pr.put("title", "Some PR title");
            pr.put("body", "Resolves BACK-55 by adding caching layer");

            WebhookPayload payload = new WebhookPayload(
                    EVcsProvider.GITHUB, "issue_comment",
                    "repo-id", "my-repo", "my-org",
                    PR_ID, "feature/other", "main", "abc123", root
            );

            WebhookResult result = processor.process(payload, project, eventConsumer, Map.of());

            assertThat(result.success()).isTrue();
            verify(taskManagementClient).getTaskDetails("BACK-55");
        }

        @Test
        @DisplayName("should return error when task ID cannot be extracted")
        void shouldReturnErrorWhenNoTaskId() {
            WebhookPayload payload = createPayload("feature/no-ticket-here");

            WebhookResult result = processor.process(payload, project, eventConsumer, Map.of());

            assertThat(result.success()).isFalse();
            assertThat(result.message()).contains("Could not extract a task ID");
        }

        @Test
        @DisplayName("should return error when branch is null")
        void shouldReturnErrorWhenBranchNull() {
            WebhookPayload payload = new WebhookPayload(
                    EVcsProvider.GITHUB, "issue_comment", "repo-id", "my-repo", "my-org",
                    PR_ID, null, "main", "abc123", null
            );

            WebhookResult result = processor.process(payload, project, eventConsumer, Map.of());

            assertThat(result.success()).isFalse();
            assertThat(result.message()).contains("Could not extract a task ID");
        }
    }

    @Nested
    @DisplayName("process() — happy path")
    class HappyPath {

        @Test
        @DisplayName("should generate doc and post new comment")
        void shouldGenerateAndPostNewComment() throws IOException {
            setupHappyPath();
            WebhookPayload payload = createPayload("feature/PROJ-123-add-login");

            WebhookResult result = processor.process(payload, project, eventConsumer, Map.of());

            assertThat(result.success()).isTrue();
            assertThat(result.message()).contains("posted");
            assertThat(result.data()).containsEntry("taskId", TASK_ID);
            assertThat(result.data()).containsEntry("action", "posted");
            assertThat(result.data()).containsEntry("documentationNeeded", true);

            String content = (String) result.data().get("content");
            assertThat(content).contains("QA Documentation");
            assertThat(content).contains(TASK_ID);

            // Verify generation was called with correct analysis metrics
            verify(qaDocGenerationService).generateQaDocumentation(
                    eq(project), eq(7L), eq(5), eq(2),
                    anyMap(), any(QaAutoDocConfig.class), any(TaskDetails.class), any(), any()
            );

            // Verify comment was posted (not updated)
            verify(taskManagementClient).postComment(eq(TASK_ID), contains("codecrow-qa-autodoc"));
            verify(taskManagementClient, never()).updateComment(anyString(), anyString(), anyString());
        }

        @Test
        @DisplayName("should update existing comment when marker found")
        void shouldUpdateExistingComment() throws IOException {
            setupHappyPath();
            // Override: existing comment found
            TaskComment existing = new TaskComment("c-existing", "bot", "old doc",
                    OffsetDateTime.now().minusHours(1), null);
            when(taskManagementClient.findCommentByMarker(eq(TASK_ID), anyString()))
                    .thenReturn(Optional.of(existing));
            when(taskManagementClient.updateComment(eq(TASK_ID), eq("c-existing"), anyString()))
                    .thenReturn(existing);

            WebhookPayload payload = createPayload("feature/PROJ-123-add-login");

            WebhookResult result = processor.process(payload, project, eventConsumer, Map.of());

            assertThat(result.success()).isTrue();
            assertThat(result.data()).containsEntry("action", "updated");
            verify(taskManagementClient).updateComment(eq(TASK_ID), eq("c-existing"), contains("codecrow-qa-autodoc"));
            verify(taskManagementClient, never()).postComment(anyString(), anyString());
        }

        @Test
        @DisplayName("should emit correct status events in order")
        void shouldEmitStatusEventsInOrder() throws IOException {
            setupHappyPath();
            WebhookPayload payload = createPayload("feature/PROJ-123-add-login");

            processor.process(payload, project, eventConsumer, Map.of());

            assertThat(capturedEvents).hasSizeGreaterThanOrEqualTo(4);
            assertThat(capturedEvents.get(0)).containsEntry("state", "resolving_task");
            assertThat(capturedEvents.get(1)).containsEntry("state", "fetching_task");
            assertThat(capturedEvents.get(2)).containsEntry("state", "generating_documentation");
            assertThat(capturedEvents.get(3)).containsEntry("state", "posting_comment");
        }
    }

    @Nested
    @DisplayName("process() — edge cases")
    class EdgeCases {

        @BeforeEach
        void setUpBase() {
            configureProject(enabledConfig());
            TaskManagementConnection conn = createConnection();
            when(connectionRepository.findById(CONNECTION_ID)).thenReturn(Optional.of(conn));
            when(clientFactory.createClient(any(), anyString(), anyString(), anyString()))
                    .thenReturn(taskManagementClient);
        }

        @Test
        @DisplayName("should handle task details fetch failure gracefully")
        void shouldHandleTaskDetailsFetchFailure() throws IOException {
            when(taskManagementClient.getTaskDetails(TASK_ID)).thenThrow(new IOException("Network error"));
            when(qaDocGenerationService.generateQaDocumentation(
                    any(), anyLong(), anyInt(), anyInt(), anyMap(), any(), isNull(), any(), any()
            )).thenReturn("doc without task context");
            when(taskManagementClient.findCommentByMarker(eq(TASK_ID), anyString()))
                    .thenReturn(Optional.empty());
            when(taskManagementClient.postComment(eq(TASK_ID), anyString()))
                    .thenReturn(new TaskComment("c-1", "bot", "doc", OffsetDateTime.now(), null));

            WebhookPayload payload = createPayload("feature/PROJ-123");

            WebhookResult result = processor.process(payload, project, eventConsumer, Map.of());

            assertThat(result.success()).isTrue();
            // Should still proceed with null taskDetails
            verify(qaDocGenerationService).generateQaDocumentation(
                    any(), anyLong(), anyInt(), anyInt(), anyMap(), any(), isNull(), any(), any()
            );
        }

        @Test
        @DisplayName("should return success with no-doc-needed when AI returns null")
        void shouldHandleNullDocumentation() throws IOException {
            when(taskManagementClient.getTaskDetails(TASK_ID)).thenReturn(sampleTaskDetails());
            when(qaDocGenerationService.generateQaDocumentation(
                    any(), anyLong(), anyInt(), anyInt(), anyMap(), any(), any(), any(), any()
            )).thenReturn(null);

            WebhookPayload payload = createPayload("feature/PROJ-123");

            WebhookResult result = processor.process(payload, project, eventConsumer, Map.of());

            assertThat(result.success()).isTrue();
            assertThat(result.message()).contains("no QA documentation is needed");
            assertThat(result.data()).containsEntry("documentationNeeded", false);
            verify(taskManagementClient, never()).postComment(anyString(), anyString());
        }

        @Test
        @DisplayName("should return success with no-doc-needed when AI returns blank")
        void shouldHandleBlankDocumentation() throws IOException {
            when(taskManagementClient.getTaskDetails(TASK_ID)).thenReturn(sampleTaskDetails());
            when(qaDocGenerationService.generateQaDocumentation(
                    any(), anyLong(), anyInt(), anyInt(), anyMap(), any(), any(), any(), any()
            )).thenReturn("   ");

            WebhookPayload payload = createPayload("feature/PROJ-123");

            WebhookResult result = processor.process(payload, project, eventConsumer, Map.of());

            assertThat(result.success()).isTrue();
            assertThat(result.message()).contains("no QA documentation is needed");
        }

        @Test
        @DisplayName("should handle no PR analysis data gracefully")
        void shouldHandleNoAnalysisData() throws IOException {
            when(codeAnalysisService.getPreviousVersionCodeAnalysis(PROJECT_ID, 7L))
                    .thenReturn(Optional.empty());
            when(taskManagementClient.getTaskDetails(TASK_ID)).thenReturn(sampleTaskDetails());
            when(qaDocGenerationService.generateQaDocumentation(
                    any(), eq(7L), eq(0), eq(0), anyMap(), any(), any(), any(), any()
            )).thenReturn("doc content");
            when(taskManagementClient.findCommentByMarker(eq(TASK_ID), anyString()))
                    .thenReturn(Optional.empty());
            when(taskManagementClient.postComment(eq(TASK_ID), anyString()))
                    .thenReturn(new TaskComment("c-1", "bot", "doc", OffsetDateTime.now(), null));

            WebhookPayload payload = createPayload("feature/PROJ-123");

            WebhookResult result = processor.process(payload, project, eventConsumer, Map.of());

            assertThat(result.success()).isTrue();
            // Issues and files should default to 0
            verify(qaDocGenerationService).generateQaDocumentation(
                    any(), eq(7L), eq(0), eq(0), anyMap(), any(), any(), any(), any()
            );
        }

        @Test
        @DisplayName("should handle generation service IOException")
        void shouldHandleGenerationServiceException() throws IOException {
            when(taskManagementClient.getTaskDetails(TASK_ID)).thenReturn(sampleTaskDetails());
            when(qaDocGenerationService.generateQaDocumentation(
                    any(), anyLong(), anyInt(), anyInt(), anyMap(), any(), any(), any(), any()
            )).thenThrow(new IOException("Inference orchestrator timeout"));

            WebhookPayload payload = createPayload("feature/PROJ-123");

            WebhookResult result = processor.process(payload, project, eventConsumer, Map.of());

            assertThat(result.success()).isFalse();
            assertThat(result.message()).contains("Failed to generate QA documentation");
        }

        @Test
        @DisplayName("should handle null PR number gracefully")
        void shouldHandleNullPrNumber() throws IOException {
            WebhookPayload payload = new WebhookPayload(
                    EVcsProvider.GITHUB, "issue_comment", "repo-id", "my-repo", "my-org",
                    null, "feature/PROJ-123", "main", "abc123", null
            );

            when(taskManagementClient.getTaskDetails(TASK_ID)).thenReturn(sampleTaskDetails());
            when(qaDocGenerationService.generateQaDocumentation(
                    any(), isNull(), eq(0), eq(0), anyMap(), any(), any(), any(), any()
            )).thenReturn("doc content");
            when(taskManagementClient.findCommentByMarker(eq(TASK_ID), anyString()))
                    .thenReturn(Optional.empty());
            when(taskManagementClient.postComment(eq(TASK_ID), anyString()))
                    .thenReturn(new TaskComment("c-1", "bot", "doc", OffsetDateTime.now(), null));

            WebhookResult result = processor.process(payload, project, eventConsumer, Map.of());

            assertThat(result.success()).isTrue();
            // Should not attempt to look up analysis when PR number is null
            verify(codeAnalysisService, never()).getPreviousVersionCodeAnalysis(anyLong(), anyLong());
        }
    }

    @Nested
    @DisplayName("process() — PR metadata building")
    class PrMetadata {

        @BeforeEach
        void setUpBase() {
            configureProject(enabledConfig());
            TaskManagementConnection conn = createConnection();
            when(connectionRepository.findById(CONNECTION_ID)).thenReturn(Optional.of(conn));
            when(clientFactory.createClient(any(), anyString(), anyString(), anyString()))
                    .thenReturn(taskManagementClient);
        }

        @Test
        @DisplayName("should include GitHub PR metadata in generation call")
        void shouldIncludeGitHubPrMetadata() throws IOException {
            ObjectNode root = mapper.createObjectNode();
            ObjectNode pr = root.putObject("pull_request");
            pr.put("title", "PROJ-123: Add feature");
            pr.put("body", "This PR implements feature X");

            WebhookPayload payload = new WebhookPayload(
                    EVcsProvider.GITHUB, "issue_comment", "repo-id", "my-repo", "my-org",
                    PR_ID, "feature/PROJ-123", "main", "abc123", root
            );

            when(taskManagementClient.getTaskDetails(TASK_ID)).thenReturn(sampleTaskDetails());
            when(taskManagementClient.findCommentByMarker(eq(TASK_ID), anyString()))
                    .thenReturn(Optional.empty());
            when(taskManagementClient.postComment(eq(TASK_ID), anyString()))
                    .thenReturn(new TaskComment("c-1", "bot", "doc", OffsetDateTime.now(), null));
            when(qaDocGenerationService.generateQaDocumentation(
                    any(), anyLong(), anyInt(), anyInt(), anyMap(), any(), any(), any(), any()
            )).thenReturn("doc content");

            processor.process(payload, project, eventConsumer, Map.of());

            @SuppressWarnings("unchecked")
            ArgumentCaptor<Map<String, Object>> metadataCaptor = ArgumentCaptor.forClass(Map.class);
            verify(qaDocGenerationService).generateQaDocumentation(
                    any(), anyLong(), anyInt(), anyInt(), metadataCaptor.capture(), any(), any(), any(), any()
            );

            Map<String, Object> metadata = metadataCaptor.getValue();
            assertThat(metadata).containsEntry("sourceBranch", "feature/PROJ-123");
            assertThat(metadata).containsEntry("targetBranch", "main");
            assertThat(metadata).containsEntry("commitHash", "abc123");
            assertThat(metadata).containsEntry("prTitle", "PROJ-123: Add feature");
            assertThat(metadata).containsEntry("prDescription", "This PR implements feature X");
        }
    }

    @Nested
    @DisplayName("process() via single-arg overload")
    class SingleArgOverload {

        @Test
        @DisplayName("should delegate to multi-arg overload with empty additionalData")
        void shouldDelegateToMultiArgOverload() {
            configureProject(enabledConfig());
            when(connectionRepository.findById(CONNECTION_ID)).thenReturn(Optional.empty());
            WebhookPayload payload = createPayload("feature/PROJ-123");

            // Call the 3-arg overload (no additionalData)
            WebhookResult result = processor.process(payload, project, eventConsumer);

            // Verification: it delegates internally → hits connection-not-found path
            assertThat(result.success()).isFalse();
            assertThat(result.message()).contains("connection not found");
        }
    }
}
