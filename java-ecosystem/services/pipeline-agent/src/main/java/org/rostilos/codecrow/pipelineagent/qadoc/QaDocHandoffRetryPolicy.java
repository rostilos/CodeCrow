package org.rostilos.codecrow.pipelineagent.qadoc;

import org.rostilos.codecrow.core.model.qadoc.QaDocDocument;
import org.rostilos.codecrow.core.model.qadoc.QaDocState;

import java.time.OffsetDateTime;
import java.util.Objects;

/** Identifies a durable QA document whose external task handoff is still pending. */
public final class QaDocHandoffRetryPolicy {

    private QaDocHandoffRetryPolicy() {
    }

    public static boolean shouldReuse(
            QaDocDocument document,
            QaDocState state,
            String currentCommitHash) {
        if (document == null
                || currentCommitHash == null
                || currentCommitHash.isBlank()
                || document.getMarkdownContent() == null
                || document.getMarkdownContent().isBlank()
                || !Objects.equals(normalize(document.getCommitHash()), normalize(currentCommitHash))) {
            return false;
        }

        OffsetDateTime documentGeneratedAt = document.getGeneratedAt();
        if (documentGeneratedAt == null) {
            return false;
        }
        return state == null
                || state.getLastGeneratedAt() == null
                || documentGeneratedAt.isAfter(state.getLastGeneratedAt());
    }

    private static String normalize(String value) {
        return value == null ? null : value.trim();
    }
}
