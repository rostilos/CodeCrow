package org.rostilos.codecrow.analysisengine.coverage;

import com.fasterxml.jackson.databind.MapperFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import org.rostilos.codecrow.analysisengine.execution.ImmutableExecutionManifest;
import org.rostilos.codecrow.vcsclient.diff.ExactDiffInventory;
import org.rostilos.codecrow.vcsclient.diff.ExactDiffInventoryParser;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;

/** Builds, verifies, and terminalizes one exact execution-bound coverage ledger. */
public final class CoverageLedgerService {
    private static final ObjectMapper CANONICAL_JSON = new ObjectMapper()
            .enable(MapperFeature.SORT_PROPERTIES_ALPHABETICALLY)
            .enable(SerializationFeature.ORDER_MAP_ENTRIES_BY_KEYS);

    private final CoverageLedgerPersistencePort persistence;
    private final ExactDiffInventoryParser parser;

    public CoverageLedgerService(CoverageLedgerPersistencePort persistence) {
        this(persistence, new ExactDiffInventoryParser());
    }

    CoverageLedgerService(
            CoverageLedgerPersistencePort persistence,
            ExactDiffInventoryParser parser) {
        this.persistence = Objects.requireNonNull(persistence, "persistence");
        this.parser = Objects.requireNonNull(parser, "parser");
    }

    public CoverageWorkPlan initializeOrVerify(
            ImmutableExecutionManifest manifest,
            String rawDiff,
            Set<String> eligiblePaths) {
        Objects.requireNonNull(manifest, "manifest");
        Objects.requireNonNull(rawDiff, "rawDiff");
        Set<String> eligible = Set.copyOf(
                Objects.requireNonNull(eligiblePaths, "eligiblePaths"));
        manifest.verifyRawDiff(rawDiff.getBytes(StandardCharsets.UTF_8));

        ExactDiffInventory inventory = parser.parse(rawDiff);
        if (inventory.completeness() != ExactDiffInventory.Completeness.COMPLETE) {
            throw new IllegalArgumentException(
                    "exact diff inventory is incomplete: " + inventory.gaps());
        }
        List<CoverageAnchor> anchors = anchors(manifest, inventory, eligible);
        String ledgerDigest = ledgerDigest(manifest, anchors);
        CoverageLedgerSeed seed = new CoverageLedgerSeed(
                CoverageContracts.SCHEMA_VERSION,
                manifest.executionId(),
                manifest.artifactManifestDigest(),
                manifest.diffDigest(),
                manifest.diffByteLength(),
                ledgerDigest,
                anchors);
        CoverageLedgerSnapshot durable = persistence.createOrLoad(seed);
        requireSeedIdentity(seed, durable);
        return workPlan(durable);
    }

    public CoverageLedgerSnapshot reconcileProducer(
            ImmutableExecutionManifest manifest,
            CoverageReceipt receipt) {
        Objects.requireNonNull(manifest, "manifest");
        Objects.requireNonNull(receipt, "receipt");
        CoverageLedgerSnapshot current = requireSnapshot(manifest.executionId());
        requireManifestIdentity(manifest, current);
        requireReceiptIdentity(receipt, current);
        List<CoverageDisposition> reconciled = reconcileDispositions(
                current, receipt.dispositions());
        CoverageLedgerSnapshot replacement = terminalSnapshot(current, reconciled);
        if (current.analysisState().terminal()) {
            if (current.equals(replacement)) {
                return current;
            }
            throw new IllegalStateException(
                    "terminal coverage ledger cannot be replaced");
        }
        return persistence.compareAndSet(current, replacement);
    }

    public CoverageLedgerSnapshot failOpenAnchors(
            ImmutableExecutionManifest manifest,
            String reasonCode) {
        Objects.requireNonNull(manifest, "manifest");
        CoverageContracts.requireReason(reasonCode, "reasonCode");
        CoverageLedgerSnapshot current = requireSnapshot(manifest.executionId());
        requireManifestIdentity(manifest, current);

        if (current.analysisState().terminal()) {
            boolean exactRetry = true;
            Map<String, CoverageAnchor> anchors = anchorsById(current.anchors());
            for (CoverageDisposition disposition : current.dispositions()) {
                CoverageAnchor anchor = anchors.get(disposition.anchorId());
                if (anchor.initialState().open()
                        && (disposition.state() != CoverageAnchorState.FAILED
                        || !reasonCode.equals(disposition.reasonCode()))) {
                    exactRetry = false;
                    break;
                }
            }
            if (exactRetry) {
                return current;
            }
            throw new IllegalStateException(
                    "terminal coverage ledger cannot be replaced");
        }

        List<CoverageDisposition> failed = current.dispositions().stream()
                .map(disposition -> disposition.state().open()
                        ? new CoverageDisposition(
                                disposition.anchorId(),
                                CoverageAnchorState.FAILED,
                                reasonCode)
                        : disposition)
                .toList();
        return persistence.compareAndSet(current, terminalSnapshot(current, failed));
    }

    /**
     * Durably records that a newer PR head owns publication for this execution's
     * scope without discarding any coverage evidence already produced.
     */
    public CoverageLedgerSnapshot supersede(
            String executionId,
            String reasonCode) {
        CoverageContracts.requireIdentifier(executionId, "executionId");
        CoverageContracts.requireReason(reasonCode, "reasonCode");
        CoverageLedgerSnapshot current = requireSnapshot(executionId);
        if (current.analysisState() == CoverageAnalysisState.SUPERSEDED) {
            return current;
        }

        List<CoverageDisposition> dispositions = current.dispositions().stream()
                .map(disposition -> disposition.state().open()
                        ? new CoverageDisposition(
                                disposition.anchorId(),
                                CoverageAnchorState.INCOMPLETE,
                                reasonCode)
                        : disposition)
                .toList();
        CoverageLedgerSnapshot replacement = new CoverageLedgerSnapshot(
                current.schemaVersion(),
                current.executionId(),
                current.artifactManifestDigest(),
                current.diffDigest(),
                current.diffByteLength(),
                current.ledgerDigest(),
                current.anchors(),
                dispositions,
                CoverageAnalysisState.SUPERSEDED,
                CoverageCounts.fromDispositions(dispositions));
        return persistence.compareAndSet(current, replacement);
    }

    public CoverageLedgerSnapshot requireSnapshot(String executionId) {
        CoverageContracts.requireIdentifier(executionId, "executionId");
        return persistence.findByExecutionId(executionId)
                .orElseThrow(() -> new IllegalStateException(
                        "coverage ledger is not durably persisted"));
    }

    private static List<CoverageAnchor> anchors(
            ImmutableExecutionManifest manifest,
            ExactDiffInventory inventory,
            Set<String> eligiblePaths) {
        List<CoverageAnchor> result = new ArrayList<>();
        for (ExactDiffInventory.Entry entry : inventory.entries()) {
            String changeId = sha256(String.join("\0",
                    "change",
                    nullToEmpty(entry.oldPath()),
                    nullToEmpty(entry.newPath()),
                    entry.status().name(),
                    entry.rawPatchSha256()));
            if (entry.hunks().isEmpty()) {
                result.add(anchor(
                        manifest,
                        entry,
                        null,
                        changeId,
                        CoverageAnchorKind.FILE_CHANGE,
                        eligiblePaths));
            } else {
                for (ExactDiffInventory.Hunk hunk : entry.hunks()) {
                    result.add(anchor(
                            manifest,
                            entry,
                            hunk,
                            changeId,
                            CoverageAnchorKind.TEXT_HUNK,
                            eligiblePaths));
                }
            }
        }
        return CoverageContracts.canonicalAnchors(result);
    }

    private static CoverageAnchor anchor(
            ImmutableExecutionManifest manifest,
            ExactDiffInventory.Entry entry,
            ExactDiffInventory.Hunk hunk,
            String changeId,
            CoverageAnchorKind kind,
            Set<String> eligiblePaths) {
        int oldStart = hunk == null ? 0 : hunk.oldRange().start();
        int oldCount = hunk == null ? 0 : hunk.oldRange().lineCount();
        int newStart = hunk == null ? 0 : hunk.newRange().start();
        int newCount = hunk == null ? 0 : hunk.newRange().lineCount();
        String parentHunkId = sha256(String.join("\0",
                "hunk",
                changeId,
                Integer.toString(oldStart),
                Integer.toString(oldCount),
                Integer.toString(newStart),
                Integer.toString(newCount),
                kind.name()));
        String anchorId = sha256(String.join("\0",
                "anchor", manifest.executionId(), parentHunkId));
        boolean mandatory = (entry.oldPath() != null
                && eligiblePaths.contains(entry.oldPath()))
                || (entry.newPath() != null
                && eligiblePaths.contains(entry.newPath()));
        CoverageAnchorState initialState;
        String reasonCode;
        if (!mandatory) {
            initialState = CoverageAnchorState.POLICY_EXCLUDED;
            reasonCode = CoverageContracts.POLICY_EXCLUDED_REASON;
        } else if (entry.status() == ExactDiffInventory.ChangeStatus.DELETE) {
            initialState = CoverageAnchorState.DELETED_RECORDED;
            reasonCode = CoverageContracts.DELETED_REASON;
        } else if (entry.binary()) {
            initialState = CoverageAnchorState.UNSUPPORTED;
            reasonCode = "binary_diff";
        } else if (kind == CoverageAnchorKind.FILE_CHANGE) {
            initialState = CoverageAnchorState.UNSUPPORTED;
            reasonCode = "non_text_change";
        } else {
            initialState = CoverageAnchorState.PENDING;
            reasonCode = null;
        }
        return new CoverageAnchor(
                anchorId,
                manifest.executionId(),
                parentHunkId,
                changeId,
                kind,
                entry.oldPath(),
                entry.newPath(),
                oldStart,
                oldCount,
                newStart,
                newCount,
                entry.status(),
                manifest.diffArtifactId(),
                manifest.diffDigest(),
                mandatory,
                initialState,
                reasonCode);
    }

    private static String ledgerDigest(
            ImmutableExecutionManifest manifest,
            List<CoverageAnchor> anchors) {
        Map<String, Object> document = new LinkedHashMap<>();
        document.put("schemaVersion", CoverageContracts.SCHEMA_VERSION);
        document.put("executionId", manifest.executionId());
        document.put("artifactManifestDigest", manifest.artifactManifestDigest());
        document.put("diffDigest", manifest.diffDigest());
        document.put("diffByteLength", manifest.diffByteLength());
        document.put("anchorCount", anchors.size());
        document.put("anchors", anchors);
        try {
            return sha256(CANONICAL_JSON.writeValueAsBytes(document));
        } catch (java.io.IOException error) {
            throw new IllegalStateException(
                    "coverage ledger canonical JSON is unavailable", error);
        }
    }

    private static List<CoverageDisposition> reconcileDispositions(
            CoverageLedgerSnapshot current,
            List<CoverageDisposition> received) {
        if (received.size() != current.anchors().size()) {
            throw new IllegalArgumentException(
                    "coverage receipt must account for every anchor");
        }
        Map<String, CoverageAnchor> anchors = anchorsById(current.anchors());
        Map<String, CoverageDisposition> unique = new HashMap<>();
        for (CoverageDisposition disposition : received) {
            if (unique.putIfAbsent(disposition.anchorId(), disposition) != null) {
                throw new IllegalArgumentException(
                        "coverage receipt contains a duplicate anchorId");
            }
            CoverageAnchor anchor = anchors.get(disposition.anchorId());
            if (anchor == null) {
                throw new IllegalArgumentException(
                        "coverage receipt contains an unknown anchorId");
            }
            if (!anchor.initialState().open()) {
                if (disposition.state() != anchor.initialState()
                        || !Objects.equals(
                                disposition.reasonCode(), anchor.reasonCode())) {
                    throw new IllegalArgumentException(
                            "coverage receipt replaced an immutable initial disposition");
                }
            } else if (disposition.state().open()
                    || disposition.state() == CoverageAnchorState.POLICY_EXCLUDED
                    || disposition.state() == CoverageAnchorState.DELETED_RECORDED) {
                throw new IllegalArgumentException(
                        "coverage receipt contains a nonterminal producer disposition");
            }
        }
        return CoverageContracts.canonicalDispositions(received);
    }

    private static CoverageLedgerSnapshot terminalSnapshot(
            CoverageLedgerSnapshot current,
            List<CoverageDisposition> dispositions) {
        CoverageCounts counts = CoverageCounts.fromDispositions(dispositions);
        Map<String, CoverageDisposition> byId = new HashMap<>();
        dispositions.forEach(value -> byId.put(value.anchorId(), value));
        List<CoverageDisposition> mandatory = current.anchors().stream()
                .filter(CoverageAnchor::mandatory)
                .map(anchor -> byId.get(anchor.anchorId()))
                .toList();
        CoverageAnalysisState state;
        if (mandatory.isEmpty()) {
            state = CoverageAnalysisState.EMPTY;
        } else if (mandatory.stream().allMatch(
                item -> item.state() == CoverageAnchorState.EXAMINED)) {
            state = CoverageAnalysisState.COMPLETE;
        } else if (mandatory.stream().noneMatch(
                item -> item.state() == CoverageAnchorState.EXAMINED)
                && mandatory.stream().anyMatch(
                        item -> item.state() == CoverageAnchorState.FAILED)) {
            state = CoverageAnalysisState.FAILED;
        } else {
            state = CoverageAnalysisState.PARTIAL;
        }
        return new CoverageLedgerSnapshot(
                current.schemaVersion(),
                current.executionId(),
                current.artifactManifestDigest(),
                current.diffDigest(),
                current.diffByteLength(),
                current.ledgerDigest(),
                current.anchors(),
                dispositions,
                state,
                counts);
    }

    private static Map<String, CoverageAnchor> anchorsById(
            List<CoverageAnchor> anchors) {
        Map<String, CoverageAnchor> result = new HashMap<>();
        anchors.forEach(anchor -> result.put(anchor.anchorId(), anchor));
        return result;
    }

    private static CoverageWorkPlan workPlan(CoverageLedgerSnapshot snapshot) {
        return new CoverageWorkPlan(
                snapshot.schemaVersion(),
                snapshot.executionId(),
                snapshot.artifactManifestDigest(),
                snapshot.diffDigest(),
                snapshot.diffByteLength(),
                snapshot.ledgerDigest(),
                snapshot.anchors());
    }

    private static void requireSeedIdentity(
            CoverageLedgerSeed seed,
            CoverageLedgerSnapshot snapshot) {
        if (seed.schemaVersion() != snapshot.schemaVersion()
                || !seed.executionId().equals(snapshot.executionId())
                || !seed.artifactManifestDigest().equals(
                        snapshot.artifactManifestDigest())
                || !seed.diffDigest().equals(snapshot.diffDigest())
                || seed.diffByteLength() != snapshot.diffByteLength()
                || !seed.ledgerDigest().equals(snapshot.ledgerDigest())
                || !seed.anchors().equals(snapshot.anchors())) {
            throw new IllegalStateException(
                    "durable coverage ledger conflicts with immutable seed");
        }
    }

    private static void requireManifestIdentity(
            ImmutableExecutionManifest manifest,
            CoverageLedgerSnapshot snapshot) {
        if (!manifest.executionId().equals(snapshot.executionId())
                || !manifest.artifactManifestDigest().equals(
                        snapshot.artifactManifestDigest())
                || !manifest.diffDigest().equals(snapshot.diffDigest())
                || manifest.diffByteLength() != snapshot.diffByteLength()) {
            throw new IllegalArgumentException(
                    "coverage ledger conflicts with immutable execution manifest");
        }
    }

    private static void requireReceiptIdentity(
            CoverageReceipt receipt,
            CoverageLedgerSnapshot snapshot) {
        if (receipt.schemaVersion() != snapshot.schemaVersion()
                || !receipt.executionId().equals(snapshot.executionId())
                || !receipt.artifactManifestDigest().equals(
                        snapshot.artifactManifestDigest())
                || !receipt.diffDigest().equals(snapshot.diffDigest())
                || receipt.diffByteLength() != snapshot.diffByteLength()
                || !receipt.ledgerDigest().equals(snapshot.ledgerDigest())) {
            throw new IllegalArgumentException(
                    "coverage receipt conflicts with durable ledger identity");
        }
    }

    private static String nullToEmpty(String value) {
        return value == null ? "" : value;
    }

    private static String sha256(String value) {
        return sha256(value.getBytes(StandardCharsets.UTF_8));
    }

    private static String sha256(byte[] value) {
        try {
            return java.util.HexFormat.of().formatHex(
                    MessageDigest.getInstance("SHA-256").digest(value));
        } catch (NoSuchAlgorithmException error) {
            throw new IllegalStateException("SHA-256 is unavailable", error);
        }
    }
}
