package org.rostilos.codecrow.analysisengine.coverage;

import java.util.List;

/** Complete producer receipt for one immutable work plan. */
public record CoverageReceipt(
        int schemaVersion,
        String executionId,
        String artifactManifestDigest,
        String diffDigest,
        long diffByteLength,
        String ledgerDigest,
        List<CoverageDisposition> dispositions) {

    public CoverageReceipt {
        CoverageContracts.requireSchema(schemaVersion);
        CoverageContracts.requireIdentifier(executionId, "executionId");
        CoverageContracts.requireSha256(
                artifactManifestDigest, "artifactManifestDigest");
        CoverageContracts.requireSha256(diffDigest, "diffDigest");
        CoverageContracts.requireNonNegative(diffByteLength, "diffByteLength");
        CoverageContracts.requireSha256(ledgerDigest, "ledgerDigest");
        dispositions = CoverageContracts.canonicalDispositions(dispositions);
    }
}
