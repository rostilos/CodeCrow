package org.rostilos.codecrow.scmevidence.api;

public record CommitEvidenceView(
        String commitHash,
        String patchId,
        String authorName,
        String authorEmail) {
}
