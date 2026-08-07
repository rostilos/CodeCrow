package org.rostilos.codecrow.scmevidence.api;

import java.util.List;

public record PromotionPlan(
        ReuseKind reuseKind,
        List<String> reusableCommits,
        List<String> commitsWithoutEvidence,
        boolean requiresTargetContextAnalysis) {

    public enum ReuseKind {
        EXACT_EVIDENCE_REUSE,
        PARTIAL_EVIDENCE_REUSE,
        NO_EVIDENCE_REUSE
    }
}
