package org.rostilos.codecrow.core.service.qadoc;

public record QaDocTestCase(
        String title,
        String priority,
        String functionalArea,
        String descriptionMarkdown
) {
}

