package org.rostilos.codecrow.analysisengine.coverage;

import java.util.List;

/** Immutable proposal used to atomically create or verify a coverage ledger. */
public record CoverageLedgerSeed(
        int schemaVersion,
        String executionId,
        String artifactManifestDigest,
        String diffDigest,
        long diffByteLength,
        String ledgerDigest,
        List<CoverageAnchor> anchors) {

    public CoverageLedgerSeed {
        CoverageContracts.requireSchema(schemaVersion);
        CoverageContracts.requireIdentifier(executionId, "executionId");
        CoverageContracts.requireSha256(
                artifactManifestDigest, "artifactManifestDigest");
        CoverageContracts.requireSha256(diffDigest, "diffDigest");
        CoverageContracts.requireNonNegative(diffByteLength, "diffByteLength");
        CoverageContracts.requireSha256(ledgerDigest, "ledgerDigest");
        anchors = CoverageContracts.canonicalAnchors(anchors);
        for (CoverageAnchor anchor : anchors) {
            if (!executionId.equals(anchor.executionId())) {
                throw new IllegalArgumentException(
                        "coverage anchor belongs to another execution");
            }
            if (!diffDigest.equals(anchor.sourceDigest())) {
                throw new IllegalArgumentException(
                        "coverage anchor belongs to another diff");
            }
        }
    }
}
