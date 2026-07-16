package org.rostilos.codecrow.analysisengine.coverage;

import java.util.List;
import java.util.Objects;

/** Exact aggregate of the full disposition partition. */
public record CoverageCounts(
        int inventory,
        int pending,
        int ownerPending,
        int examined,
        int incomplete,
        int unsupported,
        int failed,
        int policyExcluded,
        int deletedRecorded) {

    public CoverageCounts {
        CoverageContracts.requireNonNegative(inventory, "inventory");
        CoverageContracts.requireNonNegative(pending, "pending");
        CoverageContracts.requireNonNegative(ownerPending, "ownerPending");
        CoverageContracts.requireNonNegative(examined, "examined");
        CoverageContracts.requireNonNegative(incomplete, "incomplete");
        CoverageContracts.requireNonNegative(unsupported, "unsupported");
        CoverageContracts.requireNonNegative(failed, "failed");
        CoverageContracts.requireNonNegative(policyExcluded, "policyExcluded");
        CoverageContracts.requireNonNegative(deletedRecorded, "deletedRecorded");
        long accounted = (long) pending
                + ownerPending
                + examined
                + incomplete
                + unsupported
                + failed
                + policyExcluded
                + deletedRecorded;
        if (accounted != inventory) {
            throw new IllegalArgumentException(
                    "coverage counts do not reconcile with inventory");
        }
    }

    public static CoverageCounts fromDispositions(
            List<CoverageDisposition> dispositions) {
        Objects.requireNonNull(dispositions, "dispositions");
        int pending = 0;
        int ownerPending = 0;
        int examined = 0;
        int incomplete = 0;
        int unsupported = 0;
        int failed = 0;
        int policyExcluded = 0;
        int deletedRecorded = 0;
        for (CoverageDisposition disposition : dispositions) {
            Objects.requireNonNull(disposition, "disposition");
            switch (disposition.state()) {
                case PENDING -> pending++;
                case OWNER_PENDING -> ownerPending++;
                case EXAMINED -> examined++;
                case INCOMPLETE -> incomplete++;
                case UNSUPPORTED -> unsupported++;
                case FAILED -> failed++;
                case POLICY_EXCLUDED -> policyExcluded++;
                case DELETED_RECORDED -> deletedRecorded++;
            }
        }
        return new CoverageCounts(
                dispositions.size(),
                pending,
                ownerPending,
                examined,
                incomplete,
                unsupported,
                failed,
                policyExcluded,
                deletedRecorded);
    }
}
