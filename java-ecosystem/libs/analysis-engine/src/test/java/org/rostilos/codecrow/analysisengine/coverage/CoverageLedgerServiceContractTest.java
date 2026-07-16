package org.rostilos.codecrow.analysisengine.coverage;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.analysisengine.execution.ImmutableExecutionManifest;
import org.rostilos.codecrow.vcsclient.diff.ExactDiffInventory;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.rostilos.codecrow.analysisengine.coverage.CoverageAnalysisState.COMPLETE;
import static org.rostilos.codecrow.analysisengine.coverage.CoverageAnalysisState.EMPTY;
import static org.rostilos.codecrow.analysisengine.coverage.CoverageAnalysisState.FAILED;
import static org.rostilos.codecrow.analysisengine.coverage.CoverageAnalysisState.PARTIAL;
import static org.rostilos.codecrow.analysisengine.coverage.CoverageAnalysisState.PENDING;
import static org.rostilos.codecrow.analysisengine.coverage.CoverageAnchorKind.FILE_CHANGE;
import static org.rostilos.codecrow.analysisengine.coverage.CoverageAnchorKind.TEXT_HUNK;
import static org.rostilos.codecrow.analysisengine.coverage.CoverageAnchorState.EXAMINED;
import static org.rostilos.codecrow.analysisengine.coverage.CoverageAnchorState.POLICY_EXCLUDED;
import static org.rostilos.codecrow.analysisengine.coverage.CoverageAnchorState.UNSUPPORTED;
import static org.rostilos.codecrow.vcsclient.diff.ExactDiffInventory.ChangeStatus.DELETE;
import static org.rostilos.codecrow.vcsclient.diff.ExactDiffInventory.ChangeStatus.RENAME;

/**
 * RED domain contract for VS-05's durable, exact-hunk coverage ledger.
 *
 * <p>The persistence fake deliberately stores immutable snapshots and performs
 * compare-and-set only. Inventory parsing, canonical anchor construction,
 * receipt validation, aggregation, and terminal idempotency belong to
 * {@link CoverageLedgerService}, not to the fake.</p>
 */
class CoverageLedgerServiceContractTest {
    private static final int SCHEMA_VERSION = 1;
    private static final String BASE_SHA = "a".repeat(40);
    private static final String HEAD_SHA = "b".repeat(40);
    private static final String MERGE_BASE_SHA = "c".repeat(40);

    private static final String COMPLETE_DIFF = """
            diff --git "a/old folder/Name.java" "b/new folder/Name.java"
            similarity index 88%
            rename from "old folder/Name.java"
            rename to "new folder/Name.java"
            --- "a/old folder/Name.java"
            +++ "b/new folder/Name.java"
            @@ -4,2 +7,3 @@
             context
            -old value
            +new value
            +extra value
            diff --git a/docs/obsolete.md b/docs/obsolete.md
            deleted file mode 100644
            --- a/docs/obsolete.md
            +++ /dev/null
            @@ -3,2 +0,0 @@
            -obsolete
            -also obsolete
            diff --git a/src/App.java b/src/App.java
            index 1111111..2222222 100644
            --- a/src/App.java
            +++ b/src/App.java
            @@ -1 +1 @@
            -before one
            +after one
            @@ -10 +10 @@
            -before two
            +after two
            diff --git a/assets/logo.bin b/assets/logo.bin
            new file mode 100644
            index 0000000..0123456
            Binary files /dev/null and b/assets/logo.bin differ
            diff --git a/scripts/run.sh b/scripts/run.sh
            old mode 100644
            new mode 100755
            """;

    private static final String THREE_HUNK_DIFF = """
            diff --git a/src/Mixed.java b/src/Mixed.java
            index 1111111..2222222 100644
            --- a/src/Mixed.java
            +++ b/src/Mixed.java
            @@ -1 +1 @@
            -before one
            +after one
            @@ -10 +10 @@
            -before two
            +after two
            @@ -20 +20 @@
            -before three
            +after three
            """;

    @Test
    void createsOneCanonicalDeterministicAnchorPerHunkAndZeroHunkFileChange() {
        InMemoryCoverageLedgerPort port = new InMemoryCoverageLedgerPort();
        CoverageLedgerService service = new CoverageLedgerService(port);
        ImmutableExecutionManifest manifest = manifest("canonical", COMPLETE_DIFF);
        Set<String> eligiblePaths = Set.of(
                "new folder/Name.java",
                "docs/obsolete.md",
                "src/App.java",
                "assets/logo.bin",
                "scripts/run.sh");

        CoverageWorkPlan first = service.initializeOrVerify(
                manifest, COMPLETE_DIFF, eligiblePaths);
        CoverageWorkPlan replayedAfterRestart = new CoverageLedgerService(port)
                .initializeOrVerify(manifest, COMPLETE_DIFF, eligiblePaths);

        assertIdentity(first, manifest);
        assertThat(first.ledgerDigest()).matches("[0-9a-f]{64}");
        assertThat(first).isEqualTo(replayedAfterRestart);
        assertThat(port.createOrLoadCalls).isEqualTo(2);
        assertThat(port.proposedSeeds).hasSize(2);
        assertThat(port.proposedSeeds.get(1)).isEqualTo(port.proposedSeeds.get(0));

        // Four textual hunks plus one binary and one mode-only file-change
        // anchor. Neither unsupported representation is allowed to disappear.
        assertThat(first.anchors()).hasSize(6);
        assertThat(first.anchors())
                .filteredOn(anchor -> anchor.kind() == TEXT_HUNK)
                .hasSize(4);
        assertThat(first.anchors())
                .filteredOn(anchor -> anchor.kind() == FILE_CHANGE)
                .hasSize(2);
        assertThat(first.anchors())
                .extracting(CoverageAnchor::anchorId)
                .containsExactlyElementsOf(first.anchors().stream()
                        .map(CoverageAnchor::anchorId)
                        .sorted()
                        .toList());
        assertThat(first.anchors())
                .extracting(CoverageAnchor::anchorId)
                .doesNotHaveDuplicates()
                .allMatch(value -> value.matches("[0-9a-f]{64}"));
        assertThat(first.anchors())
                .extracting(CoverageAnchor::parentHunkId)
                .doesNotHaveDuplicates()
                .allMatch(value -> value.matches("[0-9a-f]{64}"));
        assertThat(first.anchors())
                .extracting(CoverageAnchor::changeId)
                .allMatch(value -> value.matches("[0-9a-f]{64}"));
        assertThat(first.anchors()).allSatisfy(anchor -> {
            assertThat(anchor.executionId()).isEqualTo(manifest.executionId());
            assertThat(anchor.sourceArtifactId()).isEqualTo(manifest.diffArtifactId());
            assertThat(anchor.sourceDigest()).matches("[0-9a-f]{64}");
            assertThat(anchor.mandatory()).isTrue();
        });
        assertThat(first.anchors())
                .filteredOn(anchor -> anchor.kind() == TEXT_HUNK
                        && anchor.changeStatus() != DELETE)
                .allSatisfy(anchor -> {
                    assertThat(anchor.initialState()).isEqualTo(
                            CoverageAnchorState.PENDING);
                    assertThat(anchor.reasonCode()).isNull();
                });
        assertThat(first.anchors())
                .filteredOn(anchor -> anchor.changeStatus() == DELETE)
                .singleElement()
                .satisfies(anchor -> {
                    assertThat(anchor.initialState()).isEqualTo(
                            CoverageAnchorState.DELETED_RECORDED);
                    assertThat(anchor.reasonCode()).isEqualTo(
                            "deleted_change_recorded");
                });
        assertThat(first.anchors())
                .filteredOn(anchor -> anchor.kind() == FILE_CHANGE)
                .allSatisfy(anchor -> {
                    assertThat(anchor.initialState()).isEqualTo(UNSUPPORTED);
                    assertThat(anchor.reasonCode()).isNotBlank();
                });

        CoverageLedgerSnapshot persisted = service.requireSnapshot(
                manifest.executionId());
        assertThat(persisted.analysisState()).isEqualTo(PENDING);
        assertThat(persisted.counts()).isEqualTo(
                new CoverageCounts(6, 3, 0, 0, 0, 2, 0, 0, 1));
        assertThat(persisted.ledgerDigest()).isEqualTo(first.ledgerDigest());
    }

    @Test
    void renameAndDeletionRetainBothPathSidesAndExactRanges() {
        ImmutableExecutionManifest manifest = manifest("paths", COMPLETE_DIFF);
        CoverageWorkPlan plan = new CoverageLedgerService(
                new InMemoryCoverageLedgerPort()).initializeOrVerify(
                manifest,
                COMPLETE_DIFF,
                Set.of("new folder/Name.java", "docs/obsolete.md"));

        assertThat(plan.anchors())
                .filteredOn(anchor -> anchor.changeStatus() == RENAME)
                .singleElement()
                .satisfies(anchor -> {
                    assertThat(anchor.kind()).isEqualTo(TEXT_HUNK);
                    assertThat(anchor.oldPath()).isEqualTo("old folder/Name.java");
                    assertThat(anchor.newPath()).isEqualTo("new folder/Name.java");
                    assertThat(anchor.oldStart()).isEqualTo(4);
                    assertThat(anchor.oldLineCount()).isEqualTo(2);
                    assertThat(anchor.newStart()).isEqualTo(7);
                    assertThat(anchor.newLineCount()).isEqualTo(3);
                });
        assertThat(plan.anchors())
                .filteredOn(anchor -> anchor.changeStatus() == DELETE)
                .singleElement()
                .satisfies(anchor -> {
                    assertThat(anchor.kind()).isEqualTo(TEXT_HUNK);
                    assertThat(anchor.oldPath()).isEqualTo("docs/obsolete.md");
                    assertThat(anchor.newPath()).isNull();
                    assertThat(anchor.oldStart()).isEqualTo(3);
                    assertThat(anchor.oldLineCount()).isEqualTo(2);
                    assertThat(anchor.newStart()).isZero();
                    assertThat(anchor.newLineCount()).isZero();
                });
        assertThat(plan.anchors())
                .filteredOn(anchor -> anchor.kind() == FILE_CHANGE)
                .allSatisfy(anchor -> {
                    assertThat(anchor.oldStart()).isZero();
                    assertThat(anchor.oldLineCount()).isZero();
                    assertThat(anchor.newStart()).isZero();
                    assertThat(anchor.newLineCount()).isZero();
                });
    }

    @Test
    void rejectsIncompleteInventoryBeforeCallingPersistence() {
        String malformedDiff = """
                diff --git a/src/App.java b/src/App.java
                provider stopped before returning a patch
                """;
        ImmutableExecutionManifest manifest = manifest("incomplete", malformedDiff);
        InMemoryCoverageLedgerPort port = new InMemoryCoverageLedgerPort();
        CoverageLedgerService service = new CoverageLedgerService(port);

        assertThatThrownBy(() -> service.initializeOrVerify(
                manifest, malformedDiff, Set.of("src/App.java")))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("incomplete");
        assertThat(port.createOrLoadCalls).isZero();
        assertThat(port.findByExecutionId(manifest.executionId())).isEmpty();
    }

    @Test
    void mixedExaminedUnsupportedAndFailedReceiptDerivesExactPartialCounts() {
        InMemoryCoverageLedgerPort port = new InMemoryCoverageLedgerPort();
        CoverageLedgerService service = new CoverageLedgerService(port);
        ImmutableExecutionManifest manifest = manifest("mixed", THREE_HUNK_DIFF);
        CoverageWorkPlan plan = service.initializeOrVerify(
                manifest, THREE_HUNK_DIFF, Set.of("src/Mixed.java"));
        List<CoverageAnchor> anchors = plan.anchors();

        CoverageReceipt receipt = receipt(
                plan,
                List.of(
                        new CoverageDisposition(anchors.get(0).anchorId(), EXAMINED, null),
                        new CoverageDisposition(
                                anchors.get(1).anchorId(),
                                UNSUPPORTED,
                                "language_adapter_unavailable"),
                        new CoverageDisposition(
                                anchors.get(2).anchorId(),
                                CoverageAnchorState.FAILED,
                                "model_batch_failed")));

        CoverageLedgerSnapshot partial = service.reconcileProducer(manifest, receipt);

        assertThat(partial.analysisState()).isEqualTo(PARTIAL);
        assertThat(partial.counts()).isEqualTo(
                new CoverageCounts(3, 0, 0, 1, 0, 1, 1, 0, 0));
        assertThat(partial.dispositions()).containsExactlyElementsOf(
                receipt.dispositions().stream()
                        .sorted(Comparator.comparing(CoverageDisposition::anchorId))
                        .toList());
        assertThat(partial.ledgerDigest()).isEqualTo(plan.ledgerDigest());
        assertThat(service.requireSnapshot(manifest.executionId())).isEqualTo(partial);
    }

    @Test
    void allFailedReceiptDerivesFailedAndAllExaminedDerivesComplete() {
        InMemoryCoverageLedgerPort port = new InMemoryCoverageLedgerPort();
        CoverageLedgerService service = new CoverageLedgerService(port);
        ImmutableExecutionManifest failedManifest = manifest("all-failed", THREE_HUNK_DIFF);
        CoverageWorkPlan failedPlan = service.initializeOrVerify(
                failedManifest, THREE_HUNK_DIFF, Set.of("src/Mixed.java"));

        CoverageLedgerSnapshot failed = service.reconcileProducer(
                failedManifest,
                receipt(failedPlan, failedPlan.anchors().stream()
                        .map(anchor -> new CoverageDisposition(
                                anchor.anchorId(),
                                CoverageAnchorState.FAILED,
                                "producer_failed"))
                        .toList()));

        assertThat(failed.analysisState()).isEqualTo(FAILED);
        assertThat(failed.counts()).isEqualTo(
                new CoverageCounts(3, 0, 0, 0, 0, 0, 3, 0, 0));

        String oneHunkDiff = """
                diff --git a/src/Healthy.java b/src/Healthy.java
                --- a/src/Healthy.java
                +++ b/src/Healthy.java
                @@ -1 +1 @@
                -before
                +after
                """;
        ImmutableExecutionManifest completeManifest = manifest("complete", oneHunkDiff);
        CoverageWorkPlan completePlan = service.initializeOrVerify(
                completeManifest, oneHunkDiff, Set.of("src/Healthy.java"));
        CoverageReceipt completeReceipt = receipt(
                completePlan,
                List.of(new CoverageDisposition(
                        completePlan.anchors().get(0).anchorId(), EXAMINED, null)));

        CoverageLedgerSnapshot complete = service.reconcileProducer(
                completeManifest, completeReceipt);

        assertThat(complete.analysisState()).isEqualTo(COMPLETE);
        assertThat(complete.counts()).isEqualTo(
                new CoverageCounts(1, 0, 0, 1, 0, 0, 0, 0, 0));
        // An exact retry of the same terminal receipt is idempotent.
        assertThat(service.reconcileProducer(completeManifest, completeReceipt))
                .isEqualTo(complete);
    }

    @Test
    void noEligibleMandatoryAnchorIsEmptyButPolicyExcludedAnchorRemainsDurable() {
        String diff = """
                diff --git a/generated/Schema.java b/generated/Schema.java
                --- a/generated/Schema.java
                +++ b/generated/Schema.java
                @@ -1 +1 @@
                -before
                +after
                """;
        InMemoryCoverageLedgerPort port = new InMemoryCoverageLedgerPort();
        CoverageLedgerService service = new CoverageLedgerService(port);
        ImmutableExecutionManifest excludedManifest = manifest("excluded", diff);

        CoverageWorkPlan excluded = service.initializeOrVerify(
                excludedManifest, diff, Set.of());

        assertThat(excluded.anchors()).singleElement().satisfies(anchor -> {
            assertThat(anchor.mandatory()).isFalse();
            assertThat(anchor.initialState()).isEqualTo(POLICY_EXCLUDED);
            assertThat(anchor.reasonCode()).isEqualTo("not_eligible_by_product_policy");
        });
        CoverageLedgerSnapshot excludedSnapshot = service.requireSnapshot(
                excludedManifest.executionId());
        assertThat(excludedSnapshot.analysisState()).isEqualTo(EMPTY);
        assertThat(excludedSnapshot.counts()).isEqualTo(
                new CoverageCounts(1, 0, 0, 0, 0, 0, 0, 1, 0));

        ImmutableExecutionManifest emptyManifest = manifest("empty", "");
        CoverageWorkPlan empty = service.initializeOrVerify(
                emptyManifest, "", Set.of());
        assertThat(empty.anchors()).isEmpty();
        assertThat(service.requireSnapshot(emptyManifest.executionId()).analysisState())
                .isEqualTo(EMPTY);
    }

    @Test
    void missingDuplicateAndUnknownReceiptAnchorsAreRejectedThenFailOpenIsDurable() {
        InMemoryCoverageLedgerPort port = new InMemoryCoverageLedgerPort();
        CoverageLedgerService service = new CoverageLedgerService(port);
        ImmutableExecutionManifest manifest = manifest("receipt-errors", THREE_HUNK_DIFF);
        CoverageWorkPlan plan = service.initializeOrVerify(
                manifest, THREE_HUNK_DIFF, Set.of("src/Mixed.java"));
        List<CoverageAnchor> anchors = plan.anchors();

        assertThatThrownBy(() -> service.reconcileProducer(
                manifest,
                receipt(plan, List.of(
                        new CoverageDisposition(anchors.get(0).anchorId(), EXAMINED, null),
                        new CoverageDisposition(anchors.get(1).anchorId(), EXAMINED, null)))))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("every anchor");
        assertThatThrownBy(() -> service.reconcileProducer(
                manifest,
                receipt(plan, List.of(
                        new CoverageDisposition(anchors.get(0).anchorId(), EXAMINED, null),
                        new CoverageDisposition(anchors.get(0).anchorId(), EXAMINED, null),
                        new CoverageDisposition(anchors.get(2).anchorId(), EXAMINED, null)))))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("duplicate");
        assertThatThrownBy(() -> service.reconcileProducer(
                manifest,
                receipt(plan, List.of(
                        new CoverageDisposition(anchors.get(0).anchorId(), EXAMINED, null),
                        new CoverageDisposition(anchors.get(1).anchorId(), EXAMINED, null),
                        new CoverageDisposition(
                                "f".repeat(64), EXAMINED, null)))))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("unknown");

        assertThat(service.requireSnapshot(manifest.executionId()).analysisState())
                .isEqualTo(PENDING);
        CoverageLedgerSnapshot failedOpen = service.failOpenAnchors(
                manifest, "producer_receipt_invalid");
        assertThat(failedOpen.analysisState()).isEqualTo(FAILED);
        assertThat(failedOpen.counts()).isEqualTo(
                new CoverageCounts(3, 0, 0, 0, 0, 0, 3, 0, 0));
        assertThat(failedOpen.dispositions()).allSatisfy(disposition -> {
            assertThat(disposition.state()).isEqualTo(CoverageAnchorState.FAILED);
            assertThat(disposition.reasonCode()).isEqualTo("producer_receipt_invalid");
        });
        assertThat(service.failOpenAnchors(manifest, "producer_receipt_invalid"))
                .isEqualTo(failedOpen);
    }

    @Test
    void supersessionIsDurableIdempotentAndPreservesCompletedCoverageEvidence() {
        InMemoryCoverageLedgerPort port = new InMemoryCoverageLedgerPort();
        CoverageLedgerService service = new CoverageLedgerService(port);
        ImmutableExecutionManifest pendingManifest = manifest(
                "superseded-pending", THREE_HUNK_DIFF);
        service.initializeOrVerify(
                pendingManifest, THREE_HUNK_DIFF, Set.of("src/Mixed.java"));

        CoverageLedgerSnapshot supersededPending = service.supersede(
                pendingManifest.executionId(), "analysis_superseded");

        assertThat(supersededPending.analysisState())
                .isEqualTo(CoverageAnalysisState.SUPERSEDED);
        assertThat(supersededPending.dispositions()).allSatisfy(disposition -> {
            assertThat(disposition.state()).isEqualTo(CoverageAnchorState.INCOMPLETE);
            assertThat(disposition.reasonCode()).isEqualTo("analysis_superseded");
        });
        assertThat(service.supersede(
                pendingManifest.executionId(), "analysis_superseded"))
                .isEqualTo(supersededPending);

        ImmutableExecutionManifest completeManifest = manifest(
                "superseded-complete", THREE_HUNK_DIFF);
        CoverageWorkPlan completePlan = service.initializeOrVerify(
                completeManifest, THREE_HUNK_DIFF, Set.of("src/Mixed.java"));
        CoverageLedgerSnapshot complete = service.reconcileProducer(
                completeManifest,
                receipt(
                        completePlan,
                        completePlan.anchors().stream()
                                .map(anchor -> new CoverageDisposition(
                                        anchor.anchorId(), EXAMINED, null))
                                .toList()));

        CoverageLedgerSnapshot supersededComplete = service.supersede(
                completeManifest.executionId(), "analysis_superseded");

        assertThat(complete.analysisState()).isEqualTo(COMPLETE);
        assertThat(supersededComplete.analysisState())
                .isEqualTo(CoverageAnalysisState.SUPERSEDED);
        assertThat(supersededComplete.dispositions())
                .isEqualTo(complete.dispositions());
        assertThat(supersededComplete.counts()).isEqualTo(complete.counts());
    }

    @Test
    void conflictingTerminalReplacementIsRejectedWithoutChangingDurableTruth() {
        InMemoryCoverageLedgerPort port = new InMemoryCoverageLedgerPort();
        CoverageLedgerService service = new CoverageLedgerService(port);
        ImmutableExecutionManifest manifest = manifest("terminal", THREE_HUNK_DIFF);
        CoverageWorkPlan plan = service.initializeOrVerify(
                manifest, THREE_HUNK_DIFF, Set.of("src/Mixed.java"));
        CoverageReceipt failedReceipt = receipt(
                plan,
                plan.anchors().stream()
                        .map(anchor -> new CoverageDisposition(
                                anchor.anchorId(),
                                CoverageAnchorState.FAILED,
                                "producer_failed"))
                        .toList());
        CoverageLedgerSnapshot terminal = service.reconcileProducer(
                manifest, failedReceipt);
        CoverageReceipt contradictoryReceipt = receipt(
                plan,
                plan.anchors().stream()
                        .map(anchor -> new CoverageDisposition(
                                anchor.anchorId(), EXAMINED, null))
                        .toList());

        assertThatThrownBy(() -> service.reconcileProducer(
                manifest, contradictoryReceipt))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("terminal");
        assertThatThrownBy(() -> service.failOpenAnchors(
                manifest, "different_terminal_reason"))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("terminal");
        assertThat(service.requireSnapshot(manifest.executionId())).isEqualTo(terminal);
    }

    private static CoverageReceipt receipt(
            CoverageWorkPlan plan,
            List<CoverageDisposition> dispositions) {
        return new CoverageReceipt(
                plan.schemaVersion(),
                plan.executionId(),
                plan.artifactManifestDigest(),
                plan.diffDigest(),
                plan.diffByteLength(),
                plan.ledgerDigest(),
                dispositions);
    }

    private static void assertIdentity(
            CoverageWorkPlan plan,
            ImmutableExecutionManifest manifest) {
        assertThat(plan.schemaVersion()).isEqualTo(SCHEMA_VERSION);
        assertThat(plan.executionId()).isEqualTo(manifest.executionId());
        assertThat(plan.artifactManifestDigest())
                .isEqualTo(manifest.artifactManifestDigest());
        assertThat(plan.diffDigest()).isEqualTo(manifest.diffDigest());
        assertThat(plan.diffByteLength()).isEqualTo(manifest.diffByteLength());
    }

    private static ImmutableExecutionManifest manifest(String suffix, String rawDiff) {
        byte[] rawBytes = rawDiff.getBytes(StandardCharsets.UTF_8);
        return ImmutableExecutionManifest.create(
                ImmutableExecutionManifest.CURRENT_SCHEMA_VERSION,
                "execution-vs05-" + suffix,
                7L,
                "github:codecrow/review-fixture",
                42L,
                BASE_SHA,
                HEAD_SHA,
                MERGE_BASE_SHA,
                "diff-vs05-" + suffix,
                sha256(rawBytes),
                rawBytes.length,
                ImmutableExecutionManifest.RAW_DIFF_ARTIFACT_KIND,
                "java-vcs-acquisition",
                "vs05-v1",
                ImmutableExecutionManifest.CURRENT_ARTIFACT_SCHEMA_VERSION,
                "candidate-review-v2",
                "creation:vs05:" + suffix,
                Instant.parse("2026-07-16T12:00:00Z"));
    }

    private static String sha256(byte[] value) {
        try {
            return java.util.HexFormat.of().formatHex(
                    MessageDigest.getInstance("SHA-256").digest(value));
        } catch (NoSuchAlgorithmException error) {
            throw new IllegalStateException("SHA-256 is unavailable", error);
        }
    }

    /** Minimal atomic fake; all domain decisions remain in the service. */
    private static final class InMemoryCoverageLedgerPort
            implements CoverageLedgerPersistencePort {
        private final Map<String, CoverageLedgerSnapshot> snapshots =
                new LinkedHashMap<>();
        private final List<CoverageLedgerSeed> proposedSeeds = new ArrayList<>();
        private int createOrLoadCalls;

        @Override
        public CoverageLedgerSnapshot createOrLoad(CoverageLedgerSeed seed) {
            createOrLoadCalls++;
            proposedSeeds.add(seed);
            CoverageLedgerSnapshot proposed = initialSnapshot(seed);
            CoverageLedgerSnapshot existing = snapshots.putIfAbsent(
                    seed.executionId(), proposed);
            if (existing == null) {
                return proposed;
            }
            if (!sameImmutableLedger(existing, seed)) {
                throw new IllegalStateException(
                        "execution already owns a different coverage ledger");
            }
            return existing;
        }

        @Override
        public Optional<CoverageLedgerSnapshot> findByExecutionId(String executionId) {
            return Optional.ofNullable(snapshots.get(executionId));
        }

        @Override
        public CoverageLedgerSnapshot compareAndSet(
                CoverageLedgerSnapshot expected,
                CoverageLedgerSnapshot replacement) {
            CoverageLedgerSnapshot current = snapshots.get(expected.executionId());
            if (!expected.equals(current)) {
                throw new IllegalStateException("coverage ledger changed concurrently");
            }
            if (isTerminal(current.analysisState())
                    && replacement.analysisState()
                    != CoverageAnalysisState.SUPERSEDED
                    && !current.equals(replacement)) {
                throw new IllegalStateException(
                        "terminal coverage ledger cannot be replaced");
            }
            snapshots.put(expected.executionId(), replacement);
            return replacement;
        }

        private static CoverageLedgerSnapshot initialSnapshot(CoverageLedgerSeed seed) {
            List<CoverageDisposition> dispositions = seed.anchors().stream()
                    .map(anchor -> new CoverageDisposition(
                            anchor.anchorId(),
                            anchor.initialState(),
                            anchor.reasonCode()))
                    .sorted(Comparator.comparing(CoverageDisposition::anchorId))
                    .toList();
            CoverageCounts counts = counts(dispositions);
            CoverageAnalysisState state = seed.anchors().stream()
                    .noneMatch(CoverageAnchor::mandatory)
                    ? EMPTY
                    : PENDING;
            return new CoverageLedgerSnapshot(
                    seed.schemaVersion(),
                    seed.executionId(),
                    seed.artifactManifestDigest(),
                    seed.diffDigest(),
                    seed.diffByteLength(),
                    seed.ledgerDigest(),
                    seed.anchors(),
                    dispositions,
                    state,
                    counts);
        }

        private static CoverageCounts counts(List<CoverageDisposition> dispositions) {
            int pending = 0;
            int ownerPending = 0;
            int examined = 0;
            int incomplete = 0;
            int unsupported = 0;
            int failed = 0;
            int policyExcluded = 0;
            int deletedRecorded = 0;
            for (CoverageDisposition disposition : dispositions) {
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

        private static boolean sameImmutableLedger(
                CoverageLedgerSnapshot snapshot,
                CoverageLedgerSeed seed) {
            return snapshot.schemaVersion() == seed.schemaVersion()
                    && snapshot.executionId().equals(seed.executionId())
                    && snapshot.artifactManifestDigest().equals(
                            seed.artifactManifestDigest())
                    && snapshot.diffDigest().equals(seed.diffDigest())
                    && snapshot.diffByteLength() == seed.diffByteLength()
                    && snapshot.ledgerDigest().equals(seed.ledgerDigest())
                    && snapshot.anchors().equals(seed.anchors());
        }

        private static boolean isTerminal(CoverageAnalysisState state) {
            return state == EMPTY
                    || state == PARTIAL
                    || state == FAILED
                    || state == COMPLETE
                    || state == CoverageAnalysisState.SUPERSEDED;
        }
    }
}
