package org.rostilos.codecrow.events.analysis;

import org.junit.jupiter.api.Test;

import java.time.Duration;
import java.util.HashMap;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class AnalysisCompletedEventTest {

    @Test
    void testEventCreation_AllFields() {
        Object source = new Object();
        String correlationId = "test-correlation-123";
        Long projectId = 1L;
        Long jobId = 100L;
        Duration duration = Duration.ofMinutes(5);
        Map<String, Object> metrics = new HashMap<>();
        metrics.put("complexity", 10);
        metrics.put("linesAnalyzed", 1000);

        AnalysisCompletedEvent event = new AnalysisCompletedEvent(
                source, correlationId, projectId, jobId,
                AnalysisCompletedEvent.CompletionStatus.SUCCESS,
                duration, 5, 20, null, metrics
        );

        assertThat(event.getSource()).isEqualTo(source);
        assertThat(event.getCorrelationId()).isEqualTo(correlationId);
        assertThat(event.getProjectId()).isEqualTo(projectId);
        assertThat(event.getJobId()).isEqualTo(jobId);
        assertThat(event.getStatus()).isEqualTo(AnalysisCompletedEvent.CompletionStatus.SUCCESS);
        assertThat(event.getDuration()).isEqualTo(duration);
        assertThat(event.getIssuesFound()).isEqualTo(5);
        assertThat(event.getFilesAnalyzed()).isEqualTo(20);
        assertThat(event.getErrorMessage()).isNull();
        assertThat(event.getMetrics()).isEqualTo(metrics);
        assertThat(event.getEventType()).isEqualTo("ANALYSIS_COMPLETED");
        assertThat(event.getEventId()).isNotNull();
        assertThat(event.getEventTimestamp()).isNotNull();
        assertThat(event.getExecutionId()).isNull();
        assertThat(event.getArtifactManifestDigest()).isNull();
    }

    @Test
    void testEventCreation_WithError() {
        Object source = new Object();
        String errorMessage = "Analysis failed due to timeout";

        AnalysisCompletedEvent event = new AnalysisCompletedEvent(
                source, null, 2L, 200L,
                AnalysisCompletedEvent.CompletionStatus.FAILED,
                Duration.ofSeconds(30), 0, 0, errorMessage, null
        );

        assertThat(event.getStatus()).isEqualTo(AnalysisCompletedEvent.CompletionStatus.FAILED);
        assertThat(event.getErrorMessage()).isEqualTo(errorMessage);
        assertThat(event.getIssuesFound()).isEqualTo(0);
        assertThat(event.getCorrelationId()).isEqualTo(event.getEventId());
    }

    @Test
    void testCompletionStatus_AllValues() {
        assertThat(AnalysisCompletedEvent.CompletionStatus.values()).containsExactly(
                AnalysisCompletedEvent.CompletionStatus.SUCCESS,
                AnalysisCompletedEvent.CompletionStatus.PARTIAL_SUCCESS,
                AnalysisCompletedEvent.CompletionStatus.FAILED,
                AnalysisCompletedEvent.CompletionStatus.CANCELLED
        );
    }

    @Test
    void testEventType() {
        AnalysisCompletedEvent event = new AnalysisCompletedEvent(
                this, null, 1L, 1L,
                AnalysisCompletedEvent.CompletionStatus.SUCCESS,
                Duration.ofMinutes(1), 0, 0, null, null
        );

        assertThat(event.getEventType()).isEqualTo("ANALYSIS_COMPLETED");
    }

    @Test
    void testPartialSuccess() {
        AnalysisCompletedEvent event = new AnalysisCompletedEvent(
                this, "corr-123", 5L, 500L,
                AnalysisCompletedEvent.CompletionStatus.PARTIAL_SUCCESS,
                Duration.ofMinutes(3), 10, 50, "Some files skipped", null
        );

        assertThat(event.getStatus()).isEqualTo(AnalysisCompletedEvent.CompletionStatus.PARTIAL_SUCCESS);
        assertThat(event.getIssuesFound()).isEqualTo(10);
        assertThat(event.getFilesAnalyzed()).isEqualTo(50);
    }

    @Test
    void successfulPredicateCoversEveryCompletionStatus() {
        assertThat(eventWithStatus(AnalysisCompletedEvent.CompletionStatus.SUCCESS).isSuccessful()).isTrue();
        assertThat(eventWithStatus(AnalysisCompletedEvent.CompletionStatus.PARTIAL_SUCCESS).isSuccessful()).isTrue();
        assertThat(eventWithStatus(AnalysisCompletedEvent.CompletionStatus.FAILED).isSuccessful()).isFalse();
        assertThat(eventWithStatus(AnalysisCompletedEvent.CompletionStatus.CANCELLED).isSuccessful()).isFalse();
    }

    // ── PR metadata (new constructor) tests ──────────────────────────────────

    @Test
    void testNewConstructor_WithPrMetadata() {
        AnalysisCompletedEvent event = new AnalysisCompletedEvent(
                this, "corr-pr", 1L, 10L,
                AnalysisCompletedEvent.CompletionStatus.SUCCESS,
                Duration.ofSeconds(60), 3, 8, null, Map.of(),
                "my-workspace", "my-project", 42L
        );

        assertThat(event.getProjectWorkspace()).isEqualTo("my-workspace");
        assertThat(event.getProjectNamespace()).isEqualTo("my-project");
        assertThat(event.getPrNumber()).isEqualTo(42L);
        assertThat(event.getProjectId()).isEqualTo(1L);
        assertThat(event.getStatus()).isEqualTo(AnalysisCompletedEvent.CompletionStatus.SUCCESS);
    }

    @Test
    void testNewConstructor_WithNullPrMetadata() {
        AnalysisCompletedEvent event = new AnalysisCompletedEvent(
                this, "corr-branch", 2L, 20L,
                AnalysisCompletedEvent.CompletionStatus.SUCCESS,
                Duration.ofSeconds(30), 1, 5, null, Map.of(),
                null, null, null
        );

        assertThat(event.getProjectWorkspace()).isNull();
        assertThat(event.getProjectNamespace()).isNull();
        assertThat(event.getPrNumber()).isNull();
    }

    @SuppressWarnings("deprecation")
    @Test
    void testDeprecatedConstructor_PrMetadataIsNull() {
        AnalysisCompletedEvent event = new AnalysisCompletedEvent(
                this, "corr-old", 3L, 30L,
                AnalysisCompletedEvent.CompletionStatus.SUCCESS,
                Duration.ofSeconds(10), 0, 2, null, null
        );

        assertThat(event.getProjectWorkspace()).isNull();
        assertThat(event.getProjectNamespace()).isNull();
        assertThat(event.getPrNumber()).isNull();
    }

    @Test
    void immutableExecutionBindingIsFirstClass() {
        String digest = "b".repeat(64);

        AnalysisCompletedEvent event = boundEvent("pr:execution-1", digest);

        assertThat(event.getExecutionId()).isEqualTo("pr:execution-1");
        assertThat(event.getArtifactManifestDigest()).isEqualTo(digest);
    }

    @Test
    void immutableExecutionBindingRejectsHalfOrInvalidIdentity() {
        String digest = "b".repeat(64);

        assertThatThrownBy(() -> boundEvent(null, digest))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("executionId");
        assertThatThrownBy(() -> boundEvent(" ", digest))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("executionId");
        assertThatThrownBy(() -> boundEvent("pr:execution-1", null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("artifactManifestDigest");
        assertThatThrownBy(() -> boundEvent("pr:execution-1", "B".repeat(64)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("artifactManifestDigest");
    }

    private AnalysisCompletedEvent boundEvent(String executionId, String digest) {
        return new AnalysisCompletedEvent(
                this,
                "corr-bound",
                1L,
                null,
                AnalysisCompletedEvent.CompletionStatus.SUCCESS,
                Duration.ofSeconds(1),
                0,
                0,
                null,
                Map.of(),
                "workspace",
                "namespace",
                42L,
                executionId,
                digest);
    }

    private AnalysisCompletedEvent eventWithStatus(
            AnalysisCompletedEvent.CompletionStatus status) {
        return new AnalysisCompletedEvent(
                this, "status", 1L, 2L, status, Duration.ZERO,
                0, 0, null, Map.of());
    }
}
