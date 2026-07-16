package org.rostilos.codecrow.analysisengine.coverage;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashSet;
import java.util.List;
import java.util.Objects;
import java.util.Set;
import java.util.regex.Pattern;

final class CoverageContracts {
    static final int SCHEMA_VERSION = 1;
    static final String POLICY_EXCLUDED_REASON = "not_eligible_by_product_policy";
    static final String DELETED_REASON = "deleted_change_recorded";

    private static final Pattern IDENTIFIER = Pattern.compile(
            "[A-Za-z0-9][A-Za-z0-9._:-]{0,159}");
    private static final Pattern SHA_256 = Pattern.compile("[0-9a-f]{64}");
    private static final Pattern REASON = Pattern.compile("[a-z][a-z0-9_.-]{0,95}");

    private CoverageContracts() {
    }

    static void requireSchema(int schemaVersion) {
        if (schemaVersion != SCHEMA_VERSION) {
            throw new IllegalArgumentException("coverage schemaVersion is unsupported");
        }
    }

    static String requireIdentifier(String value, String field) {
        if (value == null || !IDENTIFIER.matcher(value).matches()) {
            throw new IllegalArgumentException(field + " is invalid");
        }
        return value;
    }

    static String requireSha256(String value, String field) {
        if (value == null || !SHA_256.matcher(value).matches()) {
            throw new IllegalArgumentException(field + " must be a lowercase SHA-256 digest");
        }
        return value;
    }

    static String requireReason(String value, String field) {
        if (value == null || !REASON.matcher(value).matches()) {
            throw new IllegalArgumentException(field + " is invalid");
        }
        return value;
    }

    static void requireReasonForState(CoverageAnchorState state, String reasonCode) {
        Objects.requireNonNull(state, "state");
        if (state == CoverageAnchorState.PENDING
                || state == CoverageAnchorState.OWNER_PENDING
                || state == CoverageAnchorState.EXAMINED) {
            if (reasonCode != null) {
                throw new IllegalArgumentException(
                        state + " coverage state cannot carry a reasonCode");
            }
            return;
        }
        requireReason(reasonCode, "reasonCode");
    }

    static void requireNonNegative(long value, String field) {
        if (value < 0) {
            throw new IllegalArgumentException(field + " must not be negative");
        }
    }

    static List<CoverageAnchor> canonicalAnchors(List<CoverageAnchor> values) {
        List<CoverageAnchor> anchors = new ArrayList<>(List.copyOf(
                Objects.requireNonNull(values, "anchors")));
        anchors.sort(Comparator.comparing(CoverageAnchor::anchorId));
        Set<String> anchorIds = new HashSet<>();
        Set<String> parentHunkIds = new HashSet<>();
        for (CoverageAnchor anchor : anchors) {
            Objects.requireNonNull(anchor, "anchor");
            if (!anchorIds.add(anchor.anchorId())) {
                throw new IllegalArgumentException(
                        "coverage ledger contains a duplicate anchorId");
            }
            if (!parentHunkIds.add(anchor.parentHunkId())) {
                throw new IllegalArgumentException(
                        "coverage ledger contains a duplicate parentHunkId");
            }
        }
        return List.copyOf(anchors);
    }

    static List<CoverageDisposition> canonicalDispositions(
            List<CoverageDisposition> values) {
        List<CoverageDisposition> dispositions = new ArrayList<>(List.copyOf(
                Objects.requireNonNull(values, "dispositions")));
        dispositions.sort(Comparator.comparing(CoverageDisposition::anchorId));
        for (CoverageDisposition disposition : dispositions) {
            Objects.requireNonNull(disposition, "disposition");
        }
        return List.copyOf(dispositions);
    }
}
