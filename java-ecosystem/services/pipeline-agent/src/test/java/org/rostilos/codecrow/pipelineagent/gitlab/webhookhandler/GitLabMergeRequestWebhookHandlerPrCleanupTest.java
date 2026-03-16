package org.rostilos.codecrow.pipelineagent.gitlab.webhookhandler;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.analysisengine.processor.analysis.PullRequestAnalysisProcessor;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.analysisapi.rag.RagOperationsService;
import org.rostilos.codecrow.pipelineagent.generic.dto.webhook.WebhookPayload;
import org.rostilos.codecrow.pipelineagent.generic.webhookhandler.WebhookHandler.WebhookResult;
import org.springframework.test.util.ReflectionTestUtils;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
@DisplayName("GitLabMergeRequestWebhookHandler — MR RAG Cleanup")
class GitLabMergeRequestWebhookHandlerPrCleanupTest {

    @Mock private PullRequestAnalysisProcessor pullRequestAnalysisProcessor;
    @Mock private VcsServiceFactory vcsServiceFactory;
    @Mock private AnalysisLockService analysisLockService;
    @Mock private RagOperationsService ragOperationsService;

    private GitLabMergeRequestWebhookHandler handler;
    private Project project;
    private final ObjectMapper mapper = new ObjectMapper();

    @BeforeEach
    void setUp() {
        handler = new GitLabMergeRequestWebhookHandler(
                pullRequestAnalysisProcessor,
                vcsServiceFactory,
                analysisLockService,
                ragOperationsService
        );

        project = new Project();
        ReflectionTestUtils.setField(project, "id", 1L);
    }

    private WebhookPayload createMrPayload(String action, String prId) {
        ObjectNode root = mapper.createObjectNode();
        ObjectNode objAttrs = root.putObject("object_attributes");
        objAttrs.put("action", action);

        return new WebhookPayload(
                EVcsProvider.GITLAB, "merge_request", "repo-123", "my-repo", "my-group",
                prId, "feature-branch", "main", "abc123", root
        );
    }

    @Nested
    @DisplayName("MR merged")
    class MrMerged {

        @Test
        @DisplayName("should cleanup RAG data when MR is merged")
        void shouldCleanupOnMerge() {
            when(ragOperationsService.deletePrFiles(project, 10)).thenReturn(true);
            WebhookPayload payload = createMrPayload("merge", "10");

            WebhookResult result = handler.handle(payload, project, null);

            assertThat(result.status()).isEqualTo("ignored");
            assertThat(result.message()).contains("cleaned up PR RAG data");
            verify(ragOperationsService).deletePrFiles(project, 10);
        }
    }

    @Nested
    @DisplayName("MR closed (without merge)")
    class MrClosed {

        @Test
        @DisplayName("should cleanup RAG data when MR is closed")
        void shouldCleanupOnClose() {
            when(ragOperationsService.deletePrFiles(project, 10)).thenReturn(true);
            WebhookPayload payload = createMrPayload("close", "10");

            WebhookResult result = handler.handle(payload, project, null);

            assertThat(result.status()).isEqualTo("ignored");
            assertThat(result.message()).contains("cleaned up PR RAG data");
            verify(ragOperationsService).deletePrFiles(project, 10);
        }

        @Test
        @DisplayName("should not fail when cleanup returns false")
        void shouldNotFailWhenCleanupReturnsFalse() {
            when(ragOperationsService.deletePrFiles(project, 10)).thenReturn(false);
            WebhookPayload payload = createMrPayload("close", "10");

            WebhookResult result = handler.handle(payload, project, null);

            assertThat(result.success()).isTrue();
            verify(ragOperationsService).deletePrFiles(project, 10);
        }

        @Test
        @DisplayName("should not fail when cleanup throws exception")
        void shouldNotFailWhenCleanupThrows() {
            when(ragOperationsService.deletePrFiles(project, 10))
                    .thenThrow(new RuntimeException("Network failure"));
            WebhookPayload payload = createMrPayload("close", "10");

            WebhookResult result = handler.handle(payload, project, null);

            assertThat(result.success()).isTrue();
        }

        @Test
        @DisplayName("should handle null pullRequestId gracefully")
        void shouldHandleNullPrId() {
            WebhookPayload payload = createMrPayload("close", null);

            WebhookResult result = handler.handle(payload, project, null);

            assertThat(result.success()).isTrue();
            verify(ragOperationsService, never()).deletePrFiles(any(), anyInt());
        }
    }

    @Nested
    @DisplayName("Non-close actions")
    class NonCloseActions {

        @Test
        @DisplayName("should not cleanup for 'update' action")
        void shouldNotCleanupForUpdate() {
            ObjectNode root = mapper.createObjectNode();
            ObjectNode objAttrs = root.putObject("object_attributes");
            objAttrs.put("action", "update");

            WebhookPayload payload = new WebhookPayload(
                    EVcsProvider.GITLAB, "merge_request", "repo-123", "my-repo", "my-group",
                    "10", "feature", "main", "abc123", root
            );

            // Will proceed to analysis flow, may fail due to missing project setup — acceptable
            try {
                handler.handle(payload, project, null);
            } catch (Exception ignored) {
            }

            verify(ragOperationsService, never()).deletePrFiles(any(), anyInt());
        }

        @Test
        @DisplayName("should not cleanup for 'open' action")
        void shouldNotCleanupForOpen() {
            ObjectNode root = mapper.createObjectNode();
            ObjectNode objAttrs = root.putObject("object_attributes");
            objAttrs.put("action", "open");

            WebhookPayload payload = new WebhookPayload(
                    EVcsProvider.GITLAB, "merge_request", "repo-123", "my-repo", "my-group",
                    "10", "feature", "main", "abc123", root
            );

            try {
                handler.handle(payload, project, null);
            } catch (Exception ignored) {
            }

            verify(ragOperationsService, never()).deletePrFiles(any(), anyInt());
        }
    }

    @Nested
    @DisplayName("supportsEvent()")
    class SupportsEvent {

        @Test
        @DisplayName("should support merge_request event")
        void shouldSupportMergeRequestEvent() {
            assertThat(handler.supportsEvent("merge_request")).isTrue();
        }

        @Test
        @DisplayName("should not support push event")
        void shouldNotSupportPushEvent() {
            assertThat(handler.supportsEvent("push")).isFalse();
        }
    }
}
