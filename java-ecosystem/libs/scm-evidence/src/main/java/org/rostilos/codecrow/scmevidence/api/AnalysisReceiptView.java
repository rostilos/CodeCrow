package org.rostilos.codecrow.scmevidence.api;

public record AnalysisReceiptView(
        String commitHash,
        String patchId,
        String sourceBranch,
        String targetBranch,
        String targetBaseRevision,
        Long analysisId,
        String analysisType) {
}
