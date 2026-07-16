package org.rostilos.codecrow.events.analysis;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class AnalysisStartedEventTest {

    @Test
    void testEventCreation_WithCorrelationId() {
        Object source = new Object();
        String correlationId = "test-correlation-456";
        Long projectId = 10L;
        String projectName = "test-project";
        Long jobId = 1000L;

        AnalysisStartedEvent event = new AnalysisStartedEvent(
                source, correlationId, projectId, projectName,
                AnalysisStartedEvent.AnalysisType.PULL_REQUEST,
                "refs/heads/feature", jobId
        );

        assertThat(event.getSource()).isEqualTo(source);
        assertThat(event.getCorrelationId()).isEqualTo(correlationId);
        assertThat(event.getProjectId()).isEqualTo(projectId);
        assertThat(event.getProjectName()).isEqualTo(projectName);
        assertThat(event.getAnalysisType()).isEqualTo(AnalysisStartedEvent.AnalysisType.PULL_REQUEST);
        assertThat(event.getTargetRef()).isEqualTo("refs/heads/feature");
        assertThat(event.getJobId()).isEqualTo(jobId);
        assertThat(event.getEventType()).isEqualTo("ANALYSIS_STARTED");
        assertThat(event.getEventId()).isNotNull();
        assertThat(event.getEventTimestamp()).isNotNull();
        assertThat(event.getExecutionId()).isNull();
        assertThat(event.getArtifactManifestDigest()).isNull();
    }

    @Test
    void testEventCreation_WithoutCorrelationId() {
        Object source = new Object();

        AnalysisStartedEvent event = new AnalysisStartedEvent(
                source, 5L, "my-project",
                AnalysisStartedEvent.AnalysisType.BRANCH,
                "main", 50L
        );

        assertThat(event.getCorrelationId()).isEqualTo(event.getEventId());
        assertThat(event.getProjectId()).isEqualTo(5L);
        assertThat(event.getAnalysisType()).isEqualTo(AnalysisStartedEvent.AnalysisType.BRANCH);
    }

    @Test
    void testAnalysisType_AllValues() {
        assertThat(AnalysisStartedEvent.AnalysisType.values()).containsExactly(
                AnalysisStartedEvent.AnalysisType.PULL_REQUEST,
                AnalysisStartedEvent.AnalysisType.BRANCH,
                AnalysisStartedEvent.AnalysisType.COMMIT,
                AnalysisStartedEvent.AnalysisType.FULL_PROJECT
        );
    }

    @Test
    void testEventType() {
        AnalysisStartedEvent event = new AnalysisStartedEvent(
                this, 1L, "project", AnalysisStartedEvent.AnalysisType.COMMIT, "abc123", 1L
        );

        assertThat(event.getEventType()).isEqualTo("ANALYSIS_STARTED");
    }

    @Test
    void testFullProjectAnalysis() {
        AnalysisStartedEvent event = new AnalysisStartedEvent(
                this, "corr-789", 99L, "large-project",
                AnalysisStartedEvent.AnalysisType.FULL_PROJECT,
                "master", 9999L
        );

        assertThat(event.getAnalysisType()).isEqualTo(AnalysisStartedEvent.AnalysisType.FULL_PROJECT);
        assertThat(event.getTargetRef()).isEqualTo("master");
    }

    @Test
    void immutableExecutionBindingIsFirstClass() {
        String digest = "a".repeat(64);

        AnalysisStartedEvent event = new AnalysisStartedEvent(
                this,
                "corr-bound",
                1L,
                "project",
                AnalysisStartedEvent.AnalysisType.PULL_REQUEST,
                "feature",
                null,
                "pr:execution-1",
                digest);

        assertThat(event.getExecutionId()).isEqualTo("pr:execution-1");
        assertThat(event.getArtifactManifestDigest()).isEqualTo(digest);
    }

    @Test
    void immutableExecutionBindingRejectsHalfOrInvalidIdentity() {
        String digest = "a".repeat(64);

        assertThatThrownBy(() -> boundEvent(null, digest))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("executionId");
        assertThatThrownBy(() -> boundEvent(" ", digest))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("executionId");
        assertThatThrownBy(() -> boundEvent("pr:execution-1", null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("artifactManifestDigest");
        assertThatThrownBy(() -> boundEvent("pr:execution-1", "A".repeat(64)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("artifactManifestDigest");
    }

    private AnalysisStartedEvent boundEvent(String executionId, String digest) {
        return new AnalysisStartedEvent(
                this,
                "corr-bound",
                1L,
                "project",
                AnalysisStartedEvent.AnalysisType.PULL_REQUEST,
                "feature",
                null,
                executionId,
                digest);
    }
}
