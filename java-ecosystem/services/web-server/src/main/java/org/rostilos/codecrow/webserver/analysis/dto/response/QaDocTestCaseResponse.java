package org.rostilos.codecrow.webserver.analysis.dto.response;

import org.rostilos.codecrow.core.service.qadoc.QaDocTestCase;

public record QaDocTestCaseResponse(
        String title,
        String priority,
        String functionalArea,
        String descriptionMarkdown
) {
    public static QaDocTestCaseResponse fromTestCase(QaDocTestCase testCase) {
        return new QaDocTestCaseResponse(
                testCase.title(),
                testCase.priority(),
                testCase.functionalArea(),
                testCase.descriptionMarkdown()
        );
    }
}

