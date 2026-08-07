package org.rostilos.codecrow.scmevidence.api;

public record IssueProvenance(
        String commitHash,
        String authorName,
        String authorEmail,
        String filePath,
        int lineNumber,
        String confidence) {
}
