package org.rostilos.codecrow.webserver.analysis.dto.response;

import org.rostilos.codecrow.core.model.qadoc.QaDocDocument;
import org.rostilos.codecrow.core.service.qadoc.QaDocContent;
import org.rostilos.codecrow.core.service.qadoc.QaDocContentParser;

import java.time.OffsetDateTime;
import java.util.List;

public record QaDocDocumentResponse(
        boolean available,
        Long prNumber,
        String taskId,
        Long lastAnalysisId,
        String commitHash,
        String markdownContent,
        String overviewMarkdown,
        List<QaDocTestCaseResponse> testCases,
        OffsetDateTime generatedAt
) {
    public static QaDocDocumentResponse missing(Long prNumber) {
        return new QaDocDocumentResponse(false, prNumber, null, null, null, null, null, List.of(), null);
    }

    public static QaDocDocumentResponse fromDocument(QaDocDocument document) {
        QaDocContent content = QaDocContentParser.parse(document.getMarkdownContent());
        return new QaDocDocumentResponse(
                true,
                document.getPrNumber(),
                document.getTaskId(),
                document.getLastAnalysisId(),
                document.getCommitHash(),
                document.getMarkdownContent(),
                content.overviewMarkdown(),
                content.testCases().stream().map(QaDocTestCaseResponse::fromTestCase).toList(),
                document.getGeneratedAt()
        );
    }
}
