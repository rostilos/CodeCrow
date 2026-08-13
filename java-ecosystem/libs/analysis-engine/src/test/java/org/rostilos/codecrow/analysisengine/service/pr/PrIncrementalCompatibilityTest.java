package org.rostilos.codecrow.analysisengine.service.pr;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.analysisengine.util.ReviewAnalysisBehavior;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisStatus;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;

import java.io.IOException;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;

class PrIncrementalCompatibilityTest {

    @Test
    void acceptsOnlySamePrBaseBehaviorAndProvenAncestry() {
        CodeAnalysis previous = previous();

        Optional<String> result = PrIncrementalCompatibility.compatiblePreviousHead(
                previous, 42L, "base", "head-2", ReviewAnalysisBehavior.DIGEST,
                (ancestor, descendant) -> ancestor.equals("head-1") && descendant.equals("head-2"));

        assertThat(result).contains("head-1");
    }

    @Test
    void baseChangeForcesFullWithoutAncestryLookup() {
        CodeAnalysis previous = previous();
        boolean[] called = {false};

        Optional<String> result = PrIncrementalCompatibility.compatiblePreviousHead(
                previous, 42L, "different-base", "head-2", ReviewAnalysisBehavior.DIGEST,
                (ancestor, descendant) -> {
                    called[0] = true;
                    return true;
                });

        assertThat(result).isEmpty();
        assertThat(called[0]).isFalse();
    }

    @Test
    void behaviorChangeOrUnsuccessfulAnalysisForcesFull() {
        CodeAnalysis previous = previous();
        assertThat(PrIncrementalCompatibility.compatiblePreviousHead(
                previous, 42L, "base", "head-2", "different-behavior",
                (ancestor, descendant) -> true)).isEmpty();

        previous.setStatus(AnalysisStatus.ERROR);
        assertThat(PrIncrementalCompatibility.compatiblePreviousHead(
                previous, 42L, "base", "head-2", ReviewAnalysisBehavior.DIGEST,
                (ancestor, descendant) -> true)).isEmpty();
    }

    @Test
    void unprovenOrUnavailableAncestryForcesFull() {
        CodeAnalysis previous = previous();
        assertThat(PrIncrementalCompatibility.compatiblePreviousHead(
                previous, 42L, "base", "head-2", ReviewAnalysisBehavior.DIGEST,
                (ancestor, descendant) -> false)).isEmpty();

        assertThat(PrIncrementalCompatibility.compatiblePreviousHead(
                previous, 42L, "base", "head-2", ReviewAnalysisBehavior.DIGEST,
                (ancestor, descendant) -> {
                    throw new IOException("provider unavailable");
                })).isEmpty();
    }

    @Test
    void missingPreviousHeadAndSameHeadForceFull() {
        CodeAnalysis previous = previous();
        previous.setCommitHash(null);
        assertThat(PrIncrementalCompatibility.compatiblePreviousHead(
                previous, 42L, "base", "head-2", ReviewAnalysisBehavior.DIGEST,
                (ancestor, descendant) -> true)).isEmpty();

        previous.setCommitHash("head-2");
        assertThat(PrIncrementalCompatibility.compatiblePreviousHead(
                previous, 42L, "base", "head-2", ReviewAnalysisBehavior.DIGEST,
                (ancestor, descendant) -> true)).isEmpty();
    }

    private CodeAnalysis previous() {
        CodeAnalysis analysis = new CodeAnalysis();
        analysis.setAnalysisType(AnalysisType.PR_REVIEW);
        analysis.setStatus(AnalysisStatus.ACCEPTED);
        analysis.setPrNumber(42L);
        analysis.setCommitHash("head-1");
        analysis.setBaseCommitHash("base");
        analysis.setAnalysisBehaviorDigest(ReviewAnalysisBehavior.DIGEST);
        return analysis;
    }
}
