package org.rostilos.codecrow.analysisengine.coverage;

import java.util.List;
import java.util.Objects;

/** Durable immutable read model returned by the coverage persistence port. */
public record CoverageLedgerSnapshot(
        int schemaVersion,
        String executionId,
        String artifactManifestDigest,
        String diffDigest,
        long diffByteLength,
        String ledgerDigest,
        List<CoverageAnchor> anchors,
        List<CoverageDisposition> dispositions,
        CoverageAnalysisState analysisState,
        CoverageCounts counts) {

    public CoverageLedgerSnapshot {
        CoverageLedgerSeed validated = new CoverageLedgerSeed(
                schemaVersion,
                executionId,
                artifactManifestDigest,
                diffDigest,
                diffByteLength,
                ledgerDigest,
                anchors);
        anchors = validated.anchors();
        dispositions = CoverageContracts.canonicalDispositions(dispositions);
        analysisState = Objects.requireNonNull(analysisState, "analysisState");
        counts = Objects.requireNonNull(counts, "counts");
        if (anchors.size() != dispositions.size()) {
            throw new IllegalArgumentException(
                    "coverage snapshot must account for every anchor");
        }
        if (!CoverageCounts.fromDispositions(dispositions).equals(counts)) {
            throw new IllegalArgumentException(
                    "coverage snapshot counts conflict with dispositions");
        }
    }
}
