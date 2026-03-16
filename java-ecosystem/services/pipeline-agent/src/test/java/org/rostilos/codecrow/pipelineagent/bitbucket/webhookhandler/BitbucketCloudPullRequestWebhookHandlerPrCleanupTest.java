package org.rostilos.codecrow.pipelineagent.bitbucket.webhookhandler;

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
@DisplayName("BitbucketCloudPullRequestWebhookHandler — PR RAG Cleanup")
class BitbucketCloudPullRequestWebhookHandlerPrCleanupTest {

    @Mock private PullRequestAnalysisProcessor pullRequestAnalysisProcessor;
    @Mock private VcsServiceFactory vcsServiceFactory;
    @Mock private AnalysisLockService analysisLockService;
    @Mock private RagOperationsService ragOperationsService;

    private BitbucketCloudPullRequestWebhookHandler handler;
    private Project project;
    private final ObjectMapper mapper = new ObjectMapper();

    @BeforeEach
    void setUp() {
        handler = new BitbucketCloudPullRequestWebhookHandler(
                pullRequestAnalysisProcessor,
                vcsServiceFactory,
                analysisLockService,
                ragOperationsService
        );

        project = new Project();
        ReflectionTestUtils.setField(project, "id", 1L);
    }

    private WebhookPayload createBitbucketPayload(String eventType, String prId) {
        ObjectNode root = mapper.createObjectNode();
        return new WebhookPayload(
                EVcsProvider.BITBUCKET_CLOUD, eventType, "repo-123", "my-repo", "my-workspace",
                prId, "feature", "main", "abc123", root
        );
    }

    @Nested
    @DisplayName("PR fulfilled (merged)")
    class PrFulfilled {

        @Test
        @DisplayName("should cleanup RAG data when PR is fulfilled")
        void shouldCleanupOnFulfilled() {
            when(ragOperationsService.deletePrFiles(project, 5)).thenReturn(true);
            WebhookPayload payload = createBitbucketPayload("pullrequest:fulfilled", "5");

            WebhookResult result = handler.handle(payload, project, null);

            assertThat(result.status()).isEqualTo("ignored");
            assertThat(result.message()).contains("cleaned up PR RAG data");
            verify(ragOperationsService).deletePrFiles(project, 5);
        }

        @Test
        @DisplayName("should not fail when cleanup returns false on fulfilled")
        void shouldNotFailWhenCleanupReturnsFalse() {
            when(ragOperationsService.deletePrFiles(project, 5)).thenReturn(false);
            WebhookPayload payload = createBitbucketPayload("pullrequest:fulfilled", "5");

            WebhookResult result = handler.handle(payload, project, null);

            assertThat(result.success()).isTrue();
            verify(ragOperationsService).deletePrFiles(project, 5);
        }
    }

    @Nested
    @DisplayName("PR rejected (declined)")
    class PrRejected {

        @Test
        @DisplayName("should cleanup RAG data when PR is rejected")
        void shouldCleanupOnRejected() {
            when(ragOperationsService.deletePrFiles(project, 7)).thenReturn(true);
            WebhookPayload payload = createBitbucketPayload("pullrequest:rejected", "7");

            WebhookResult result = handler.handle(payload, project, null);

            assertThat(result.status()).isEqualTo("ignored");
            assertThat(result.message()).contains("cleaned up PR RAG data");
            verify(ragOperationsService).deletePrFiles(project, 7);
        }

        @Test
        @DisplayName("should not fail when cleanup throws exception")
        void shouldNotFailWhenCleanupThrows() {
            when(ragOperationsService.deletePrFiles(project, 7))
                    .thenThrow(new RuntimeException("Connection refused"));
            WebhookPayload payload = createBitbucketPayload("pullrequest:rejected", "7");

            WebhookResult result = handler.handle(payload, project, null);

            assertThat(result.success()).isTrue();
        }

        @Test
        @DisplayName("should handle null pullRequestId gracefully")
        void shouldHandleNullPrId() {
            WebhookPayload payload = createBitbucketPayload("pullrequest:rejected", null);

            WebhookResult result = handler.handle(payload, project, null);

            assertThat(result.success()).isTrue();
            verify(ragOperationsService, never()).deletePrFiles(any(), anyInt());
        }
    }

    @Nested
    @DisplayName("supportsEvent()")
    class SupportsEvent {

        @Test
        @DisplayName("should support pullrequest:created event")
        void shouldSupportCreated() {
            assertThat(handler.supportsEvent("pullrequest:created")).isTrue();
        }

        @Test
        @DisplayName("should support pullrequest:updated event")
        void shouldSupportUpdated() {
            assertThat(handler.supportsEvent("pullrequest:updated")).isTrue();
        }

        @Test
        @DisplayName("should support pullrequest:fulfilled event")
        void shouldSupportFulfilled() {
            assertThat(handler.supportsEvent("pullrequest:fulfilled")).isTrue();
        }

        @Test
        @DisplayName("should support pullrequest:rejected event")
        void shouldSupportRejected() {
            assertThat(handler.supportsEvent("pullrequest:rejected")).isTrue();
        }

        @Test
        @DisplayName("should not support push event")
        void shouldNotSupportPush() {
            assertThat(handler.supportsEvent("repo:push")).isFalse();
        }
    }

    @Nested
    @DisplayName("Non-close events (should not trigger cleanup)")
    class NonCloseEvents {

        @Test
        @DisplayName("should not cleanup on pullrequest:created")
        void shouldNotCleanupOnCreated() {
            WebhookPayload payload = createBitbucketPayload("pullrequest:created", "5");

            // Will proceed to analysis flow, may fail due to missing project setup — acceptable
            try {
                handler.handle(payload, project, null);
            } catch (Exception ignored) {
            }

            verify(ragOperationsService, never()).deletePrFiles(any(), anyInt());
        }

        @Test
        @DisplayName("should not cleanup on pullrequest:updated")
        void shouldNotCleanupOnUpdated() {
            WebhookPayload payload = createBitbucketPayload("pullrequest:updated", "5");

            try {
                handler.handle(payload, project, null);
            } catch (Exception ignored) {
            }

            verify(ragOperationsService, never()).deletePrFiles(any(), anyInt());
        }
    }
}
