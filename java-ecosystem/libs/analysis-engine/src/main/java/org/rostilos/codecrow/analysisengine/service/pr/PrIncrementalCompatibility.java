package org.rostilos.codecrow.analysisengine.service.pr;

import org.rostilos.codecrow.core.model.codeanalysis.AnalysisStatus;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.util.Optional;

/**
 * Decides whether a persisted PR review is a safe incremental predecessor.
 * Unavailable or ambiguous lineage always falls back to a full review.
 */
public final class PrIncrementalCompatibility {

    private static final Logger log = LoggerFactory.getLogger(PrIncrementalCompatibility.class);

    private PrIncrementalCompatibility() {
    }

    public static Optional<String> compatiblePreviousHead(
            CodeAnalysis previous,
            Long currentPrNumber,
            String currentBaseCommit,
            String currentHeadCommit,
            String currentBehaviorDigest,
            CommitAncestryVerifier ancestryVerifier) {
        if (previous == null
                || previous.getAnalysisType() != AnalysisType.PR_REVIEW
                || previous.getStatus() != AnalysisStatus.ACCEPTED
                || currentPrNumber == null
                || !currentPrNumber.equals(previous.getPrNumber())
                || !sameNonBlank(currentBaseCommit, previous.getBaseCommitHash())
                || !sameNonBlank(currentBehaviorDigest, previous.getAnalysisBehaviorDigest())
                || !hasText(previous.getCommitHash())
                || !hasText(currentHeadCommit)
                || previous.getCommitHash().equals(currentHeadCommit)) {
            return Optional.empty();
        }

        try {
            if (!ancestryVerifier.isAncestor(previous.getCommitHash(), currentHeadCommit)) {
                log.info("PR incremental predecessor {} is not a proven ancestor of {}; using FULL",
                        abbreviate(previous.getCommitHash()), abbreviate(currentHeadCommit));
                return Optional.empty();
            }
            return Optional.of(previous.getCommitHash());
        } catch (IOException | RuntimeException unavailable) {
            log.warn("PR incremental ancestry unavailable for {} -> {}; using FULL: {}",
                    abbreviate(previous.getCommitHash()), abbreviate(currentHeadCommit),
                    unavailable.getMessage());
            return Optional.empty();
        }
    }

    private static boolean sameNonBlank(String left, String right) {
        return hasText(left) && left.equals(right);
    }

    private static boolean hasText(String value) {
        return value != null && !value.isBlank();
    }

    private static String abbreviate(String hash) {
        return hash != null && hash.length() > 7 ? hash.substring(0, 7) : String.valueOf(hash);
    }

    @FunctionalInterface
    public interface CommitAncestryVerifier {
        boolean isAncestor(String ancestorCommit, String descendantCommit) throws IOException;
    }
}
