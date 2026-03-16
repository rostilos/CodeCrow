package org.rostilos.codecrow.ragengine.listener;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.events.analysis.AnalysisCompletedEvent;
import org.rostilos.codecrow.ragengine.client.RagPipelineClient;

import java.time.Duration;
import java.util.Map;

import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
@DisplayName("AnalysisCompletedPrCleanupListener")
class AnalysisCompletedPrCleanupListenerTest {

    @Mock
    private RagPipelineClient ragPipelineClient;

    private AnalysisCompletedPrCleanupListener listener;

    @BeforeEach
    void setUp() {
        listener = new AnalysisCompletedPrCleanupListener(ragPipelineClient);
    }

    private AnalysisCompletedEvent createEvent(String workspace, String namespace, Long prNumber) {
        return new AnalysisCompletedEvent(
                this, "corr-123", 1L, 10L,
                AnalysisCompletedEvent.CompletionStatus.SUCCESS,
                Duration.ofSeconds(30), 5, 10, null, Map.of(),
                workspace, namespace, prNumber
        );
    }

    @Nested
    @DisplayName("onAnalysisCompleted()")
    class OnAnalysisCompleted {

        @Test
        @DisplayName("should call deletePrFiles when PR metadata is present")
        void shouldCallDeletePrFilesWhenPrMetadataPresent() {
            when(ragPipelineClient.deletePrFiles("test-workspace", "test-project", 42)).thenReturn(true);

            listener.onAnalysisCompleted(createEvent("test-workspace", "test-project", 42L));

            verify(ragPipelineClient).deletePrFiles("test-workspace", "test-project", 42);
        }

        @Test
        @DisplayName("should not call deletePrFiles when prNumber is null")
        void shouldSkipWhenPrNumberIsNull() {
            AnalysisCompletedEvent event = new AnalysisCompletedEvent(
                    this, "corr-123", 1L, 10L,
                    AnalysisCompletedEvent.CompletionStatus.SUCCESS,
                    Duration.ofSeconds(30), 5, 10, null, Map.of(),
                    "ws", "proj", null
            );

            listener.onAnalysisCompleted(event);

            verifyNoInteractions(ragPipelineClient);
        }

        @Test
        @DisplayName("should not call deletePrFiles when workspace is null")
        void shouldSkipWhenWorkspaceIsNull() {
            AnalysisCompletedEvent event = new AnalysisCompletedEvent(
                    this, "corr-123", 1L, 10L,
                    AnalysisCompletedEvent.CompletionStatus.SUCCESS,
                    Duration.ofSeconds(30), 5, 10, null, Map.of(),
                    null, "proj", 42L
            );

            listener.onAnalysisCompleted(event);

            verifyNoInteractions(ragPipelineClient);
        }

        @Test
        @DisplayName("should not call deletePrFiles when namespace is null")
        void shouldSkipWhenNamespaceIsNull() {
            AnalysisCompletedEvent event = new AnalysisCompletedEvent(
                    this, "corr-123", 1L, 10L,
                    AnalysisCompletedEvent.CompletionStatus.SUCCESS,
                    Duration.ofSeconds(30), 5, 10, null, Map.of(),
                    "ws", null, 42L
            );

            listener.onAnalysisCompleted(event);

            verifyNoInteractions(ragPipelineClient);
        }

        @Test
        @DisplayName("should not call deletePrFiles for non-PR event (deprecated constructor)")
        void shouldSkipForNonPrEvent() {
            @SuppressWarnings("deprecation")
            AnalysisCompletedEvent event = new AnalysisCompletedEvent(
                    this, "corr-123", 1L, 10L,
                    AnalysisCompletedEvent.CompletionStatus.SUCCESS,
                    Duration.ofSeconds(30), 5, 10, null, Map.of()
            );

            listener.onAnalysisCompleted(event);

            verifyNoInteractions(ragPipelineClient);
        }

        @Test
        @DisplayName("should retry on failure and succeed on second attempt")
        void shouldRetryOnFailureAndSucceed() {
            when(ragPipelineClient.deletePrFiles("ws", "proj", 42))
                    .thenReturn(false)   // first attempt fails
                    .thenReturn(true);   // second attempt succeeds

            listener.onAnalysisCompleted(createEvent("ws", "proj", 42L));

            verify(ragPipelineClient, times(2)).deletePrFiles("ws", "proj", 42);
        }

        @Test
        @DisplayName("should retry on exception and succeed on second attempt")
        void shouldRetryOnExceptionAndSucceed() {
            when(ragPipelineClient.deletePrFiles("ws", "proj", 42))
                    .thenThrow(new RuntimeException("network error"))
                    .thenReturn(true);

            listener.onAnalysisCompleted(createEvent("ws", "proj", 42L));

            verify(ragPipelineClient, times(2)).deletePrFiles("ws", "proj", 42);
        }

        @Test
        @DisplayName("should exhaust retries and give up after MAX_RETRIES")
        void shouldExhaustRetriesAndGiveUp() {
            when(ragPipelineClient.deletePrFiles("ws", "proj", 42)).thenReturn(false);

            listener.onAnalysisCompleted(createEvent("ws", "proj", 42L));

            // MAX_RETRIES = 3
            verify(ragPipelineClient, times(3)).deletePrFiles("ws", "proj", 42);
        }

        @Test
        @DisplayName("should clean up on FAILED status too")
        void shouldCleanUpOnFailedStatus() {
            AnalysisCompletedEvent event = new AnalysisCompletedEvent(
                    this, "corr-123", 1L, 10L,
                    AnalysisCompletedEvent.CompletionStatus.FAILED,
                    Duration.ofSeconds(5), 0, 0, "some error", Map.of(),
                    "ws", "proj", 42L
            );
            when(ragPipelineClient.deletePrFiles("ws", "proj", 42)).thenReturn(true);

            listener.onAnalysisCompleted(event);

            verify(ragPipelineClient).deletePrFiles("ws", "proj", 42);
        }

        @Test
        @DisplayName("should clean up on CANCELLED status too")
        void shouldCleanUpOnCancelledStatus() {
            AnalysisCompletedEvent event = new AnalysisCompletedEvent(
                    this, "corr-123", 1L, 10L,
                    AnalysisCompletedEvent.CompletionStatus.CANCELLED,
                    Duration.ofSeconds(1), 0, 0, null, Map.of(),
                    "ws", "proj", 99L
            );
            when(ragPipelineClient.deletePrFiles("ws", "proj", 99)).thenReturn(true);

            listener.onAnalysisCompleted(event);

            verify(ragPipelineClient).deletePrFiles("ws", "proj", 99);
        }
    }
}
