package org.rostilos.codecrow.core.service.qadoc;

import java.util.List;

public record QaDocContent(String overviewMarkdown, List<QaDocTestCase> testCases) {
}

