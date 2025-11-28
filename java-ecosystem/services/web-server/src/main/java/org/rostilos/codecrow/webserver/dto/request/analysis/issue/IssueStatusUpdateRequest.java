package org.rostilos.codecrow.webserver.dto.request.analysis.issue;

public record IssueStatusUpdateRequest(
        boolean isResolved,
        String comment
) {
}
