package org.rostilos.codecrow.pipelineagent.qadoc;

import org.rostilos.codecrow.core.model.qadoc.QaDocDocument;
import org.rostilos.codecrow.core.model.qadoc.QaDocState;

import java.time.OffsetDateTime;
import java.util.Locale;
import java.util.Objects;

/** Identifies a durable QA document whose external task handoff is still pending. */
public final class QaDocHandoffRetryPolicy {

    private QaDocHandoffRetryPolicy() {
    }

    public static boolean shouldReuse(
            QaDocDocument document,
            QaDocState state,
            String currentCommitHash,
            String expectedTaskId) {
        if (document == null
                || currentCommitHash == null
                || currentCommitHash.isBlank()
                || expectedTaskId == null
                || expectedTaskId.isBlank()
                || document.getMarkdownContent() == null
                || document.getMarkdownContent().isBlank()
                || !Objects.equals(normalize(document.getCommitHash()), normalize(currentCommitHash))
                || !Objects.equals(
                        normalizeTaskId(document.getTaskId()),
                        normalizeTaskId(expectedTaskId))) {
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

    private static String normalizeTaskId(String value) {
        String normalized = normalize(value);
        return normalized == null ? null : normalized.toUpperCase(Locale.ROOT);
    }
}
