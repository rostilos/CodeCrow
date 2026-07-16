package org.rostilos.codecrow.analysisengine.coverage;

import java.util.List;

/** Exact immutable work document sent to the candidate worker. */
public record CoverageWorkPlan(
        int schemaVersion,
        String executionId,
        String artifactManifestDigest,
        String diffDigest,
        long diffByteLength,
        String ledgerDigest,
        List<CoverageAnchor> anchors) {

    public CoverageWorkPlan {
        CoverageLedgerSeed validated = new CoverageLedgerSeed(
                schemaVersion,
                executionId,
                artifactManifestDigest,
                diffDigest,
                diffByteLength,
                ledgerDigest,
                anchors);
        anchors = validated.anchors();
    }
}
