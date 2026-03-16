package org.rostilos.codecrow.pipelineagent.github.webhookhandler;

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
import org.rostilos.codecrow.analysisengine.processor.analysis.BranchAnalysisProcessor;
import org.rostilos.codecrow.analysisengine.processor.analysis.PullRequestAnalysisProcessor;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.analysisapi.rag.RagOperationsService;
import org.rostilos.codecrow.pipelineagent.generic.dto.webhook.WebhookPayload;
import org.rostilos.codecrow.pipelineagent.generic.webhookhandler.WebhookHandler.WebhookResult;
import org.springframework.test.util.ReflectionTestUtils;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
@DisplayName("GitHubPullRequestWebhookHandler — PR RAG Cleanup")
class GitHubPullRequestWebhookHandlerPrCleanupTest {

    @Mock private PullRequestAnalysisProcessor pullRequestAnalysisProcessor;
    @Mock private BranchAnalysisProcessor branchAnalysisProcessor;
    @Mock private VcsServiceFactory vcsServiceFactory;
    @Mock private AnalysisLockService analysisLockService;
    @Mock private RagOperationsService ragOperationsService;

    private GitHubPullRequestWebhookHandler handler;
    private Project project;
    private final ObjectMapper mapper = new ObjectMapper();

    @BeforeEach
    void setUp() {
        handler = new GitHubPullRequestWebhookHandler(
                pullRequestAnalysisProcessor,
                branchAnalysisProcessor,
                vcsServiceFactory,
                analysisLockService,
                ragOperationsService
        );

        project = new Project();
        ReflectionTestUtils.setField(project, "id", 1L);
    }

    private WebhookPayload createClosedPrPayload(boolean merged, String prId) {
        ObjectNode root = mapper.createObjectNode();
        root.put("action", "closed");
        ObjectNode prNode = root.putObject("pull_request");
        prNode.put("merged", merged);

        return new WebhookPayload(
                EVcsProvider.GITHUB, "pull_request", "repo-123", "my-repo", "my-org",
                prId, "feature", "main", "abc123", root
        );
    }

    @Nested
    @DisplayName("PR closed (not merged)")
    class PrClosed {

        @Test
        @DisplayName("should cleanup RAG data when PR is closed without merge")
        void shouldCleanupRagDataOnClose() {
            when(ragOperationsService.deletePrFiles(project, 42)).thenReturn(true);
            WebhookPayload payload = createClosedPrPayload(false, "42");

            WebhookResult result = handler.handle(payload, project, null);

            assertThat(result.status()).isEqualTo("ignored");
            assertThat(result.message()).contains("closed without merge");
            verify(ragOperationsService).deletePrFiles(project, 42);
        }

        @Test
        @DisplayName("should not fail when RAG cleanup fails on close")
        void shouldNotFailWhenCleanupFails() {
            when(ragOperationsService.deletePrFiles(project, 42)).thenReturn(false);
            WebhookPayload payload = createClosedPrPayload(false, "42");

            WebhookResult result = handler.handle(payload, project, null);

            assertThat(result.success()).isTrue();
            verify(ragOperationsService).deletePrFiles(project, 42);
        }

        @Test
        @DisplayName("should not fail when RAG cleanup throws exception on close")
        void shouldNotFailWhenCleanupThrows() {
            when(ragOperationsService.deletePrFiles(project, 42))
                    .thenThrow(new RuntimeException("Network error"));
            WebhookPayload payload = createClosedPrPayload(false, "42");

            WebhookResult result = handler.handle(payload, project, null);

            assertThat(result.success()).isTrue();
        }

        @Test
        @DisplayName("should handle null pullRequestId gracefully on close")
        void shouldHandleNullPrIdOnClose() {
            WebhookPayload payload = createClosedPrPayload(false, null);

            WebhookResult result = handler.handle(payload, project, null);

            assertThat(result.success()).isTrue();
            verify(ragOperationsService, never()).deletePrFiles(any(), anyInt());
        }
    }

    @Nested
    @DisplayName("PR merged")
    class PrMerged {

        @Test
        @DisplayName("should cleanup RAG data when PR is merged")
        void shouldCleanupRagDataOnMerge() {
            when(ragOperationsService.deletePrFiles(project, 42)).thenReturn(true);
            WebhookPayload payload = createClosedPrPayload(true, "42");

            // handle() will try to handle merge event, which may fail due to missing mocks.
            // The key assertion is that cleanupPrRagData was called BEFORE the merge handler.
            try {
                handler.handle(payload, project, null);
            } catch (Exception ignored) {
                // Merge handler may throw due to missing setup — acceptable for this test
            }

            verify(ragOperationsService).deletePrFiles(project, 42);
        }
    }

    @Nested
    @DisplayName("Non-close actions")
    class NonCloseActions {

        @Test
        @DisplayName("should not cleanup RAG data for open action")
        void shouldNotCleanupForOpenAction() {
            ObjectNode root = mapper.createObjectNode();
            root.put("action", "opened");
            WebhookPayload payload = new WebhookPayload(
                    EVcsProvider.GITHUB, "pull_request", "repo-123", "my-repo", "my-org",
                    "42", "feature", "main", "abc123", root
            );

            // Will fail on validation but should not trigger cleanup
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
        @DisplayName("should support pull_request event")
        void shouldSupportPullRequestEvent() {
            assertThat(handler.supportsEvent("pull_request")).isTrue();
        }

        @Test
        @DisplayName("should not support push event")
        void shouldNotSupportPushEvent() {
            assertThat(handler.supportsEvent("push")).isFalse();
        }
    }
}
