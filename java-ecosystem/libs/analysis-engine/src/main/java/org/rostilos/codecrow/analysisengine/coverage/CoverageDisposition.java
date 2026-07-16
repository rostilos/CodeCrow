package org.rostilos.codecrow.analysisengine.coverage;

import java.util.Objects;

/** Current per-anchor state reported or derived for one execution. */
public record CoverageDisposition(
        String anchorId,
        CoverageAnchorState state,
        String reasonCode) {

    public CoverageDisposition {
        CoverageContracts.requireSha256(anchorId, "anchorId");
        Objects.requireNonNull(state, "state");
        CoverageContracts.requireReasonForState(state, reasonCode);
    }
}
