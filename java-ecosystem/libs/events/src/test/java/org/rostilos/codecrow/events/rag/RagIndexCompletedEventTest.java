package org.rostilos.codecrow.events.rag;

import org.junit.jupiter.api.Test;

import java.time.Duration;

import static org.assertj.core.api.Assertions.assertThat;

class RagIndexCompletedEventTest {

    @Test
    void testEventCreation_Success() {
        Object source = new Object();
        String correlationId = "rag-corr-123";
        Long projectId = 5L;
        Duration duration = Duration.ofMinutes(2);

        RagIndexCompletedEvent event = new RagIndexCompletedEvent(
                source, correlationId, projectId,
                RagIndexStartedEvent.IndexType.MAIN,
                RagIndexStartedEvent.IndexOperation.CREATE,
                RagIndexCompletedEvent.CompletionStatus.SUCCESS,
                duration, 1000, null
        );

        assertThat(event.getSource()).isEqualTo(source);
        assertThat(event.getCorrelationId()).isEqualTo(correlationId);
        assertThat(event.getProjectId()).isEqualTo(projectId);
        assertThat(event.getIndexType()).isEqualTo(RagIndexStartedEvent.IndexType.MAIN);
        assertThat(event.getOperation()).isEqualTo(RagIndexStartedEvent.IndexOperation.CREATE);
        assertThat(event.getStatus()).isEqualTo(RagIndexCompletedEvent.CompletionStatus.SUCCESS);
        assertThat(event.getDuration()).isEqualTo(duration);
        assertThat(event.getChunksCreated()).isEqualTo(1000);
        assertThat(event.getErrorMessage()).isNull();
        assertThat(event.isSuccessful()).isTrue();
        assertThat(event.getEventType()).isEqualTo("RAG_INDEX_COMPLETED");
    }

    @Test
    void testEventCreation_Failed() {
        String errorMessage = "Index creation failed due to memory error";

        RagIndexCompletedEvent event = new RagIndexCompletedEvent(
                this, "corr-fail", 10L,
                RagIndexStartedEvent.IndexType.BRANCH,
                RagIndexStartedEvent.IndexOperation.UPDATE,
                RagIndexCompletedEvent.CompletionStatus.FAILED,
                Duration.ofSeconds(30), 0, errorMessage
        );

        assertThat(event.getStatus()).isEqualTo(RagIndexCompletedEvent.CompletionStatus.FAILED);
        assertThat(event.getErrorMessage()).isEqualTo(errorMessage);
        assertThat(event.getChunksCreated()).isEqualTo(0);
        assertThat(event.isSuccessful()).isFalse();
    }

    @Test
    void testEventCreation_Skipped() {
        RagIndexCompletedEvent event = new RagIndexCompletedEvent(
                this, null, 15L,
                RagIndexStartedEvent.IndexType.FULL,
                RagIndexStartedEvent.IndexOperation.DELETE,
                RagIndexCompletedEvent.CompletionStatus.SKIPPED,
                Duration.ZERO, 0, "No changes detected"
        );

        assertThat(event.getStatus()).isEqualTo(RagIndexCompletedEvent.CompletionStatus.SKIPPED);
        assertThat(event.isSuccessful()).isFalse();
        assertThat(event.getCorrelationId()).isEqualTo(event.getEventId());
    }

    @Test
    void testCompletionStatus_AllValues() {
        assertThat(RagIndexCompletedEvent.CompletionStatus.values()).containsExactly(
                RagIndexCompletedEvent.CompletionStatus.SUCCESS,
                RagIndexCompletedEvent.CompletionStatus.FAILED,
                RagIndexCompletedEvent.CompletionStatus.SKIPPED
        );
    }

    @Test
    void testBranchIndexUpdate() {
        RagIndexCompletedEvent event = new RagIndexCompletedEvent(
                this, "branch-update", 20L,
                RagIndexStartedEvent.IndexType.BRANCH,
                RagIndexStartedEvent.IndexOperation.UPDATE,
                RagIndexCompletedEvent.CompletionStatus.SUCCESS,
                Duration.ofSeconds(45), 250, null
        );

        assertThat(event.getIndexType()).isEqualTo(RagIndexStartedEvent.IndexType.BRANCH);
        assertThat(event.getOperation()).isEqualTo(RagIndexStartedEvent.IndexOperation.UPDATE);
        assertThat(event.getChunksCreated()).isEqualTo(250);
    }
}
