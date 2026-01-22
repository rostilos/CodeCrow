package org.rostilos.codecrow.events.analysis;

import org.junit.jupiter.api.Test;

import java.time.Duration;
import java.util.HashMap;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

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
}
