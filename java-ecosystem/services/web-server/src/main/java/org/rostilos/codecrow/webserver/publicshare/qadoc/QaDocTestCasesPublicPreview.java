package org.rostilos.codecrow.webserver.publicshare.qadoc;

import org.rostilos.codecrow.webserver.analysis.dto.response.QaDocTestCaseResponse;

import java.util.List;

/**
 * Least-disclosure DTO returned only after a public-share credential resolves.
 * It contains display metadata, but no internal document, project, workspace,
 * pull-request, analysis, or commit identifiers.
 */
public record QaDocTestCasesPublicPreview(
        String title,
        String projectName,
        String taskKey,
        String taskSummary,
        List<QaDocTestCaseResponse> testCases
) {
}
