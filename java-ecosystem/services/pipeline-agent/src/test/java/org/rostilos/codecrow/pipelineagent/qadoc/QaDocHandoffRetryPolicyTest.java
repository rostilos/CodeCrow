package org.rostilos.codecrow.pipelineagent.qadoc;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.qadoc.QaDocDocument;
import org.rostilos.codecrow.core.model.qadoc.QaDocState;

import java.time.OffsetDateTime;

import static org.assertj.core.api.Assertions.assertThat;

class QaDocHandoffRetryPolicyTest {

    @Test
    void reusesCurrentCommitDocumentCreatedAfterTheLastSuccessfulHandoff() {
        QaDocDocument document = document("commit-b", OffsetDateTime.parse("2026-08-12T10:05:00Z"));
        QaDocState state = state("commit-a", OffsetDateTime.parse("2026-08-12T10:00:00Z"));

        assertThat(QaDocHandoffRetryPolicy.shouldReuse(
                document, state, "commit-b", "task-1")).isTrue();
    }

    @Test
    void regeneratesAfterASuccessfulHandoffOrForAnotherCommit() {
        QaDocDocument delivered = document("commit-b", OffsetDateTime.parse("2026-08-12T10:00:00Z"));
        QaDocState state = state("commit-b", OffsetDateTime.parse("2026-08-12T10:05:00Z"));

        assertThat(QaDocHandoffRetryPolicy.shouldReuse(
                delivered, state, "commit-b", "TASK-1")).isFalse();
        assertThat(QaDocHandoffRetryPolicy.shouldReuse(
                delivered, state, "commit-c", "TASK-1")).isFalse();
    }

    @Test
    void doesNotReuseBlankDocument() {
        QaDocDocument document = document("commit-a", OffsetDateTime.now());
        document.setMarkdownContent("  ");

        assertThat(QaDocHandoffRetryPolicy.shouldReuse(
                document, null, "commit-a", "TASK-1")).isFalse();
    }

    @Test
    void doesNotReuseDocumentGeneratedForAnotherTask() {
        QaDocDocument document = document("commit-a", OffsetDateTime.now());

        assertThat(QaDocHandoffRetryPolicy.shouldReuse(
                document, null, "commit-a", "TASK-2")).isFalse();
    }

    private static QaDocDocument document(String commit, OffsetDateTime generatedAt) {
        QaDocDocument document = new QaDocDocument(null, 17L);
        document.setCommitHash(commit);
        document.setGeneratedAt(generatedAt);
        document.setMarkdownContent("# QA document");
        document.setTaskId("TASK-1");
        return document;
    }

    private static QaDocState state(String commit, OffsetDateTime generatedAt) {
        QaDocState state = new QaDocState();
        state.setLastCommitHash(commit);
        state.setLastGeneratedAt(generatedAt);
        return state;
    }
}
