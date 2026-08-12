package org.rostilos.codecrow.webserver.publicshare.qadoc;

import org.rostilos.codecrow.webserver.analysis.dto.response.QaDocTestCaseResponse;

import java.util.List;

/**
 * QA content returned only after a public-share credential resolves. It mirrors
 * the QA document tabs while excluding workspace details and internal document,
 * project, pull-request, analysis, and commit identifiers.
 */
public record QaDocPublicPreview(
        String title,
        String projectName,
        String taskKey,
        String taskSummary,
        String overviewMarkdown,
        List<QaDocTestCaseResponse> testCases,
        String environmentMarkdown
) {
}
