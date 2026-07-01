package org.rostilos.codecrow.webserver.analysis.dto.response;

import org.rostilos.codecrow.core.model.qadoc.QaDocDocument;

import java.time.OffsetDateTime;

public record QaDocDocumentResponse(
        boolean available,
        Long prNumber,
        String taskId,
        Long lastAnalysisId,
        String commitHash,
        String markdownContent,
        OffsetDateTime generatedAt
) {
    public static QaDocDocumentResponse missing(Long prNumber) {
        return new QaDocDocumentResponse(false, prNumber, null, null, null, null, null);
    }

    public static QaDocDocumentResponse fromDocument(QaDocDocument document) {
        return new QaDocDocumentResponse(
                true,
                document.getPrNumber(),
                document.getTaskId(),
                document.getLastAnalysisId(),
                document.getCommitHash(),
                document.getMarkdownContent(),
                document.getGeneratedAt()
        );
    }
}
