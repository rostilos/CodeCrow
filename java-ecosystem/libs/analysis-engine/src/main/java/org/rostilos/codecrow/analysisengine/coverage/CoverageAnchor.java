package org.rostilos.codecrow.analysisengine.coverage;

import org.rostilos.codecrow.vcsclient.diff.ExactDiffInventory;

import java.util.Objects;

/** Immutable identity and initial accounting state for one exact diff anchor. */
public record CoverageAnchor(
        String anchorId,
        String executionId,
        String parentHunkId,
        String changeId,
        CoverageAnchorKind kind,
        String oldPath,
        String newPath,
        int oldStart,
        int oldLineCount,
        int newStart,
        int newLineCount,
        ExactDiffInventory.ChangeStatus changeStatus,
        String sourceArtifactId,
        String sourceDigest,
        boolean mandatory,
        CoverageAnchorState initialState,
        String reasonCode) {

    public CoverageAnchor {
        CoverageContracts.requireSha256(anchorId, "anchorId");
        CoverageContracts.requireIdentifier(executionId, "executionId");
        CoverageContracts.requireSha256(parentHunkId, "parentHunkId");
        CoverageContracts.requireSha256(changeId, "changeId");
        Objects.requireNonNull(kind, "kind");
        if (oldPath == null && newPath == null) {
            throw new IllegalArgumentException(
                    "coverage anchor must retain an oldPath or newPath");
        }
        requirePath(oldPath, "oldPath");
        requirePath(newPath, "newPath");
        CoverageContracts.requireNonNegative(oldStart, "oldStart");
        CoverageContracts.requireNonNegative(oldLineCount, "oldLineCount");
        CoverageContracts.requireNonNegative(newStart, "newStart");
        CoverageContracts.requireNonNegative(newLineCount, "newLineCount");
        Objects.requireNonNull(changeStatus, "changeStatus");
        CoverageContracts.requireIdentifier(sourceArtifactId, "sourceArtifactId");
        CoverageContracts.requireSha256(sourceDigest, "sourceDigest");
        Objects.requireNonNull(initialState, "initialState");
        CoverageContracts.requireReasonForState(initialState, reasonCode);
        if (!mandatory && initialState != CoverageAnchorState.POLICY_EXCLUDED) {
            throw new IllegalArgumentException(
                    "nonmandatory coverage anchor must be POLICY_EXCLUDED");
        }
        if (mandatory && initialState == CoverageAnchorState.POLICY_EXCLUDED) {
            throw new IllegalArgumentException(
                    "mandatory coverage anchor cannot be POLICY_EXCLUDED");
        }
        if (kind == CoverageAnchorKind.FILE_CHANGE
                && (oldStart != 0
                || oldLineCount != 0
                || newStart != 0
                || newLineCount != 0)) {
            throw new IllegalArgumentException(
                    "FILE_CHANGE coverage anchor must use zero ranges");
        }
    }

    private static void requirePath(String path, String field) {
        if (path != null && (path.isBlank() || path.indexOf('\0') >= 0)) {
            throw new IllegalArgumentException(field + " is invalid");
        }
    }
}
