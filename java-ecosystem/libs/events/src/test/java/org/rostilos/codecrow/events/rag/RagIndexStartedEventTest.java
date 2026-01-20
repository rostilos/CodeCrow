package org.rostilos.codecrow.events.rag;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class RagIndexStartedEventTest {

    @Test
    void testEventCreation_WithCorrelationId() {
        Object source = new Object();
        String correlationId = "rag-start-123";
        Long projectId = 1L;
        String projectName = "test-project";

        RagIndexStartedEvent event = new RagIndexStartedEvent(
                source, correlationId, projectId, projectName,
                RagIndexStartedEvent.IndexType.MAIN,
                RagIndexStartedEvent.IndexOperation.CREATE,
                "main", "abc123def456"
        );

        assertThat(event.getSource()).isEqualTo(source);
        assertThat(event.getCorrelationId()).isEqualTo(correlationId);
        assertThat(event.getProjectId()).isEqualTo(projectId);
        assertThat(event.getProjectName()).isEqualTo(projectName);
        assertThat(event.getIndexType()).isEqualTo(RagIndexStartedEvent.IndexType.MAIN);
        assertThat(event.getOperation()).isEqualTo(RagIndexStartedEvent.IndexOperation.CREATE);
        assertThat(event.getBranchName()).isEqualTo("main");
        assertThat(event.getCommitHash()).isEqualTo("abc123def456");
        assertThat(event.getEventType()).isEqualTo("RAG_INDEX_STARTED");
    }

    @Test
    void testEventCreation_WithoutCorrelationId() {
        RagIndexStartedEvent event = new RagIndexStartedEvent(
                this, 5L, "my-project",
                RagIndexStartedEvent.IndexType.BRANCH,
                RagIndexStartedEvent.IndexOperation.UPDATE,
                "feature-branch", "xyz789"
        );

        assertThat(event.getCorrelationId()).isEqualTo(event.getEventId());
        assertThat(event.getIndexType()).isEqualTo(RagIndexStartedEvent.IndexType.BRANCH);
        assertThat(event.getOperation()).isEqualTo(RagIndexStartedEvent.IndexOperation.UPDATE);
    }

    @Test
    void testIndexType_AllValues() {
        assertThat(RagIndexStartedEvent.IndexType.values()).containsExactly(
                RagIndexStartedEvent.IndexType.MAIN,
                RagIndexStartedEvent.IndexType.BRANCH,
                RagIndexStartedEvent.IndexType.FULL
        );
    }

    @Test
    void testIndexOperation_AllValues() {
        assertThat(RagIndexStartedEvent.IndexOperation.values()).containsExactly(
                RagIndexStartedEvent.IndexOperation.CREATE,
                RagIndexStartedEvent.IndexOperation.UPDATE,
                RagIndexStartedEvent.IndexOperation.DELETE
        );
    }

    @Test
    void testFullIndexCreation() {
        RagIndexStartedEvent event = new RagIndexStartedEvent(
                this, "full-index", 100L, "large-project",
                RagIndexStartedEvent.IndexType.FULL,
                RagIndexStartedEvent.IndexOperation.CREATE,
                "master", "commit-hash-123"
        );

        assertThat(event.getIndexType()).isEqualTo(RagIndexStartedEvent.IndexType.FULL);
        assertThat(event.getOperation()).isEqualTo(RagIndexStartedEvent.IndexOperation.CREATE);
    }

    @Test
    void testIndexDeletion() {
        RagIndexStartedEvent event = new RagIndexStartedEvent(
                this, 10L, "old-project",
                RagIndexStartedEvent.IndexType.MAIN,
                RagIndexStartedEvent.IndexOperation.DELETE,
                null, null
        );

        assertThat(event.getOperation()).isEqualTo(RagIndexStartedEvent.IndexOperation.DELETE);
        assertThat(event.getBranchName()).isNull();
        assertThat(event.getCommitHash()).isNull();
    }
}
