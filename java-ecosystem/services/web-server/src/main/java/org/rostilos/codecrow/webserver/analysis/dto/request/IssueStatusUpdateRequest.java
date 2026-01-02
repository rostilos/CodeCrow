package org.rostilos.codecrow.webserver.analysis.dto.request;

public record IssueStatusUpdateRequest(
        boolean isResolved,
        String comment
) {
}
