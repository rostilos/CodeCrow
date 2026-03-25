package org.rostilos.codecrow.pipelineagent.generic.webhookhandler;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.CommentCommandsConfig;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.PrSummarizeCacheRepository;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.analysisengine.processor.analysis.PullRequestAnalysisProcessor;
import org.rostilos.codecrow.analysisengine.service.PromptSanitizationService;
import org.rostilos.codecrow.pipelineagent.generic.dto.webhook.WebhookPayload;
import org.rostilos.codecrow.pipelineagent.generic.service.CommandAuthorizationService;
import org.rostilos.codecrow.pipelineagent.generic.service.CommandAuthorizationService.AuthorizationResult;
import org.rostilos.codecrow.pipelineagent.generic.service.CommentCommandRateLimitService;
import org.rostilos.codecrow.pipelineagent.generic.webhookhandler.CommentCommandWebhookHandler.CommentCommandProcessor;
import org.rostilos.codecrow.pipelineagent.generic.webhookhandler.WebhookHandler.WebhookResult;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.function.Consumer;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
@DisplayName("CommentCommandWebhookHandler — QA_DOC dispatch")
class CommentCommandWebhookHandlerQaDocTest {

    @Mock private CommentCommandRateLimitService rateLimitService;
    @Mock private PromptSanitizationService sanitizationService;
    @Mock private CodeAnalysisService codeAnalysisService;
    @Mock private PrSummarizeCacheRepository summarizeCacheRepository;
    @Mock private PullRequestAnalysisProcessor pullRequestAnalysisProcessor;
    @Mock private VcsClientProvider vcsClientProvider;
    @Mock private CommandAuthorizationService authorizationService;
    @Mock private CommentCommandProcessor summarizeProcessor;
    @Mock private CommentCommandProcessor askProcessor;
    @Mock private CommentCommandProcessor qaDocProcessor;

    private CommentCommandWebhookHandler handler;
    private Project project;
    private List<Map<String, Object>> capturedEvents;
    private Consumer<Map<String, Object>> eventConsumer;

    @BeforeEach
    void setUp() {
        handler = new CommentCommandWebhookHandler(
                rateLimitService,
                sanitizationService,
                codeAnalysisService,
                summarizeCacheRepository,
                pullRequestAnalysisProcessor,
                vcsClientProvider,
                authorizationService,
                summarizeProcessor,
                askProcessor,
                qaDocProcessor
        );

        project = new Project();
        ReflectionTestUtils.setField(project, "id", 1L);

        // Configure project with enabled comment commands (all allowed)
        ProjectConfig config = new ProjectConfig();
        config.setCommentCommands(new CommentCommandsConfig(true, null, null, null, null, null, null));
        ReflectionTestUtils.setField(project, "configuration", config);

        capturedEvents = new ArrayList<>();
        eventConsumer = capturedEvents::add;

        // Default: rate limit OK (lenient because not all tests exercise this path)
        lenient().when(rateLimitService.checkRateLimit(any(Project.class)))
                .thenReturn(CommentCommandRateLimitService.RateLimitCheckResult.allowed(100));

        // Default: authorization OK (lenient because not all tests exercise this path)
        lenient().when(authorizationService.checkAuthorization(any(), any(), any(), any()))
                .thenReturn(new AuthorizationResult(true, "Authorized"));
    }

    private WebhookPayload createQaDocPayload(String commandBody) {
        WebhookPayload.CommentData comment = new WebhookPayload.CommentData(
                "comment-1", commandBody, "user-1", "johndoe",
                null, false, null, null
        );
        return new WebhookPayload(
                EVcsProvider.GITHUB, "issue_comment", "repo-123", "my-repo", "my-org",
                "42", "feature/PROJ-123", "main", "abc123", null, comment, "user-1", "johndoe"
        );
    }

    @Nested
    @DisplayName("handle() — QA_DOC dispatch")
    class QaDocDispatch {

        @Test
        @DisplayName("should dispatch /codecrow qa-doc to qaDocProcessor without arguments")
        void shouldDispatchQaDocWithoutArgs() {
            when(qaDocProcessor.process(any(), any(), any(), anyMap()))
                    .thenReturn(WebhookResult.success("QA doc generated"));

            WebhookPayload payload = createQaDocPayload("/codecrow qa-doc");

            WebhookResult result = handler.handle(payload, project, eventConsumer);

            assertThat(result.success()).isTrue();
            assertThat(result.message()).isEqualTo("QA doc generated");

            @SuppressWarnings("unchecked")
            ArgumentCaptor<Map<String, Object>> dataCaptor = ArgumentCaptor.forClass(Map.class);
            verify(qaDocProcessor).process(any(WebhookPayload.class), eq(project), any(), dataCaptor.capture());

            // No explicit task ID → additionalData should NOT contain taskId
            Map<String, Object> additionalData = dataCaptor.getValue();
            assertThat(additionalData).doesNotContainKey("taskId");
        }

        @Test
        @DisplayName("should dispatch /codecrow qa-doc PROJ-123 with task ID in additionalData")
        void shouldDispatchQaDocWithTaskId() {
            when(qaDocProcessor.process(any(), any(), any(), anyMap()))
                    .thenReturn(WebhookResult.success("QA doc generated for PROJ-123"));

            WebhookPayload payload = createQaDocPayload("/codecrow qa-doc PROJ-123");

            WebhookResult result = handler.handle(payload, project, eventConsumer);

            assertThat(result.success()).isTrue();

            @SuppressWarnings("unchecked")
            ArgumentCaptor<Map<String, Object>> dataCaptor = ArgumentCaptor.forClass(Map.class);
            verify(qaDocProcessor).process(any(WebhookPayload.class), eq(project), any(), dataCaptor.capture());

            Map<String, Object> additionalData = dataCaptor.getValue();
            assertThat(additionalData).containsEntry("taskId", "PROJ-123");
        }

        @Test
        @DisplayName("should return error when qaDocProcessor is null")
        void shouldReturnErrorWhenProcessorNull() {
            // Create handler without qa-doc processor
            CommentCommandWebhookHandler handlerNoQaDoc = new CommentCommandWebhookHandler(
                    rateLimitService, sanitizationService, codeAnalysisService,
                    summarizeCacheRepository, pullRequestAnalysisProcessor,
                    vcsClientProvider, authorizationService,
                    summarizeProcessor, askProcessor, null
            );

            WebhookPayload payload = createQaDocPayload("/codecrow qa-doc");

            WebhookResult result = handlerNoQaDoc.handle(payload, project, eventConsumer);

            assertThat(result.success()).isFalse();
            assertThat(result.message()).contains("not available");
        }

        @Test
        @DisplayName("should record command for rate limiting before dispatch")
        void shouldRecordCommandBeforeDispatch() {
            when(qaDocProcessor.process(any(), any(), any(), anyMap()))
                    .thenReturn(WebhookResult.success("done"));

            WebhookPayload payload = createQaDocPayload("/codecrow qa-doc");
            handler.handle(payload, project, eventConsumer);

            verify(rateLimitService).recordCommand(project);
        }

        @Test
        @DisplayName("should reject when rate limited")
        void shouldRejectWhenRateLimited() {
            when(rateLimitService.checkRateLimit(any(Project.class)))
                    .thenReturn(new CommentCommandRateLimitService.RateLimitCheckResult(
                            false, 0, 30L, "Rate limit exceeded"));

            WebhookPayload payload = createQaDocPayload("/codecrow qa-doc");

            WebhookResult result = handler.handle(payload, project, eventConsumer);

            assertThat(result.success()).isFalse();
            assertThat(result.message()).contains("Rate limit");
            verify(qaDocProcessor, never()).process(any(), any(), any(), anyMap());
        }

        @Test
        @DisplayName("should reject when comment commands are disabled")
        void shouldRejectWhenCommandsDisabled() {
            ProjectConfig disabledConfig = new ProjectConfig();
            disabledConfig.setCommentCommands(new CommentCommandsConfig(false, null, null, null, null, null, null));
            ReflectionTestUtils.setField(project, "configuration", disabledConfig);

            WebhookPayload payload = createQaDocPayload("/codecrow qa-doc");

            WebhookResult result = handler.handle(payload, project, eventConsumer);

            assertThat(result.success()).isFalse();
            assertThat(result.message()).contains("not enabled");
            verify(qaDocProcessor, never()).process(any(), any(), any(), anyMap());
        }
    }

    @Nested
    @DisplayName("supportsEvent()")
    class SupportsEvent {

        @Test
        @DisplayName("should support issue_comment events")
        void shouldSupportIssueComment() {
            assertThat(handler.supportsEvent("issue_comment")).isTrue();
        }

        @Test
        @DisplayName("should support pullrequest:comment_created events")
        void shouldSupportBitbucketComment() {
            assertThat(handler.supportsEvent("pullrequest:comment_created")).isTrue();
        }

        @Test
        @DisplayName("should not support push events")
        void shouldNotSupportPush() {
            assertThat(handler.supportsEvent("push")).isFalse();
        }
    }

    @Nested
    @DisplayName("handle() — ignored payloads")
    class IgnoredPayloads {

        @Test
        @DisplayName("should ignore non-command comments")
        void shouldIgnoreNonCommandComment() {
            WebhookPayload.CommentData comment = new WebhookPayload.CommentData(
                    "c-1", "Just a regular comment", "user-1", "johndoe",
                    null, false, null, null
            );
            WebhookPayload payload = new WebhookPayload(
                    EVcsProvider.GITHUB, "issue_comment", "repo-123", "my-repo", "my-org",
                    "42", "feature", "main", "abc123", null, comment
            );

            WebhookResult result = handler.handle(payload, project, eventConsumer);

            assertThat(result.status()).isEqualTo("ignored");
        }

        @Test
        @DisplayName("should ignore payload without comment data")
        void shouldIgnorePayloadWithoutCommentData() {
            WebhookPayload payload = new WebhookPayload(
                    EVcsProvider.GITHUB, "pull_request", "repo-123", "my-repo", "my-org",
                    "42", "feature", "main", "abc123", null
            );

            WebhookResult result = handler.handle(payload, project, eventConsumer);

            assertThat(result.status()).isEqualTo("ignored");
        }
    }
}
