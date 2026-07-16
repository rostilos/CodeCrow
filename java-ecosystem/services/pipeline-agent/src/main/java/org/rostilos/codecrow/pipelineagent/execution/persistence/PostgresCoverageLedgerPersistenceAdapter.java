package org.rostilos.codecrow.pipelineagent.execution.persistence;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.rostilos.codecrow.analysisengine.coverage.CoverageAnalysisState;
import org.rostilos.codecrow.analysisengine.coverage.CoverageAnchor;
import org.rostilos.codecrow.analysisengine.coverage.CoverageAnchorKind;
import org.rostilos.codecrow.analysisengine.coverage.CoverageAnchorState;
import org.rostilos.codecrow.analysisengine.coverage.CoverageCounts;
import org.rostilos.codecrow.analysisengine.coverage.CoverageDisposition;
import org.rostilos.codecrow.analysisengine.coverage.CoverageLedgerPersistencePort;
import org.rostilos.codecrow.analysisengine.coverage.CoverageLedgerSeed;
import org.rostilos.codecrow.analysisengine.coverage.CoverageLedgerSnapshot;
import org.rostilos.codecrow.vcsclient.diff.ExactDiffInventory;
import org.springframework.stereotype.Repository;

import javax.sql.DataSource;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.Set;
import java.util.TreeMap;

/** PostgreSQL-backed atomic store for the VS-05 coverage ledger. */
@Repository
public class PostgresCoverageLedgerPersistenceAdapter
        implements CoverageLedgerPersistencePort {

    private static final ObjectMapper JSON = new ObjectMapper();

    private static final String SELECT_MANIFEST_IDENTITY = """
            SELECT diff_artifact_id, diff_digest, diff_byte_length
            FROM review_execution
            WHERE id = ? AND artifact_manifest_digest = ?
            FOR KEY SHARE
            """;

    private static final String INSERT_ANALYSIS_STATE = """
            INSERT INTO review_analysis_state (
                execution_id,
                schema_version,
                artifact_manifest_digest,
                diff_digest,
                diff_byte_length,
                ledger_digest,
                analysis_state,
                inventory_anchor_count,
                pending_anchor_count,
                owner_pending_anchor_count,
                examined_anchor_count,
                incomplete_anchor_count,
                unsupported_anchor_count,
                failed_anchor_count,
                policy_excluded_anchor_count,
                deleted_recorded_anchor_count,
                reason_counts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CAST(? AS JSONB))
            ON CONFLICT (execution_id) DO NOTHING
            """;

    private static final String INSERT_ANCHOR = """
            INSERT INTO review_coverage_anchor (
                anchor_id,
                schema_version,
                execution_id,
                artifact_manifest_digest,
                diff_digest,
                diff_byte_length,
                ledger_digest,
                source_artifact_id,
                source_digest,
                parent_hunk_id,
                change_id,
                change_status,
                anchor_kind,
                old_path,
                new_path,
                old_start,
                old_line_count,
                new_start,
                new_line_count,
                mandatory,
                initial_state,
                initial_reason_code
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """;

    private static final String SELECT_ANALYSIS_STATE = """
            SELECT
                schema_version,
                execution_id,
                artifact_manifest_digest,
                diff_digest,
                diff_byte_length,
                ledger_digest,
                analysis_state,
                inventory_anchor_count,
                pending_anchor_count,
                owner_pending_anchor_count,
                examined_anchor_count,
                incomplete_anchor_count,
                unsupported_anchor_count,
                failed_anchor_count,
                policy_excluded_anchor_count,
                deleted_recorded_anchor_count,
                revision
            FROM review_analysis_state
            WHERE execution_id = ?
            """;

    private static final String SELECT_ANCHORS = """
            SELECT
                schema_version,
                anchor_id,
                execution_id,
                artifact_manifest_digest,
                diff_digest,
                diff_byte_length,
                ledger_digest,
                source_artifact_id,
                source_digest,
                parent_hunk_id,
                change_id,
                change_status,
                anchor_kind,
                old_path,
                new_path,
                old_start,
                old_line_count,
                new_start,
                new_line_count,
                mandatory,
                initial_state,
                initial_reason_code
            FROM review_coverage_anchor
            WHERE execution_id = ?
            ORDER BY anchor_id
            """;

    private static final String SELECT_DISPOSITIONS = """
            SELECT anchor_id, coverage_state, reason_code
            FROM review_coverage_disposition
            WHERE execution_id = ?
            ORDER BY anchor_id
            """;

    private static final String DELETE_DISPOSITIONS = """
            DELETE FROM review_coverage_disposition
            WHERE execution_id = ?
            """;

    private static final String INSERT_DISPOSITION = """
            INSERT INTO review_coverage_disposition (
                execution_id,
                artifact_manifest_digest,
                ledger_digest,
                anchor_id,
                coverage_state,
                reason_code
            ) VALUES (?, ?, ?, ?, ?, ?)
            """;

    private static final String UPDATE_ANALYSIS_STATE = """
            UPDATE review_analysis_state
            SET analysis_state = ?,
                inventory_anchor_count = ?,
                pending_anchor_count = ?,
                owner_pending_anchor_count = ?,
                examined_anchor_count = ?,
                incomplete_anchor_count = ?,
                unsupported_anchor_count = ?,
                failed_anchor_count = ?,
                policy_excluded_anchor_count = ?,
                deleted_recorded_anchor_count = ?,
                reason_counts = CAST(? AS JSONB),
                revision = revision + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE execution_id = ? AND revision = ?
            """;

    private final DataSource dataSource;

    public PostgresCoverageLedgerPersistenceAdapter(DataSource dataSource) {
        this.dataSource = Objects.requireNonNull(dataSource, "dataSource");
    }

    @Override
    public CoverageLedgerSnapshot createOrLoad(CoverageLedgerSeed seed) {
        Objects.requireNonNull(seed, "seed");
        requireCanonicalAnchors(seed);
        CoverageLedgerSnapshot initial = initialSnapshot(seed);

        try (Connection connection = dataSource.getConnection()) {
            begin(connection);
            try {
                requireManifestIdentity(connection, seed);
                int inserted = insertAnalysisState(connection, initial);
                if (inserted == 1) {
                    for (CoverageAnchor anchor : seed.anchors()) {
                        insertAnchor(connection, seed, anchor);
                    }
                    replaceDispositions(connection, initial);
                }

                StoredSnapshot stored = load(connection, seed.executionId(), true)
                        .orElseThrow(() -> new IllegalStateException(
                                "coverage create-or-load returned no durable state"));
                requireSeedReceipt(seed, stored.snapshot());
                if (inserted == 1 && !initial.equals(stored.snapshot())) {
                    throw new IllegalStateException(
                            "new coverage ledger does not match its immutable seed");
                }
                connection.commit();
                return stored.snapshot();
            } catch (SQLException error) {
                rollback(connection, error);
                throw persistenceFailure("atomic coverage create-or-load failed", error);
            } catch (RuntimeException error) {
                rollback(connection, error);
                throw error;
            }
        } catch (SQLException error) {
            throw persistenceFailure("unable to access coverage ledger storage", error);
        }
    }

    @Override
    public Optional<CoverageLedgerSnapshot> findByExecutionId(String executionId) {
        requireExecutionId(executionId);
        try (Connection connection = dataSource.getConnection()) {
            beginRead(connection);
            try {
                Optional<CoverageLedgerSnapshot> snapshot = load(
                        connection, executionId, false).map(StoredSnapshot::snapshot);
                connection.commit();
                return snapshot;
            } catch (SQLException error) {
                rollback(connection, error);
                throw persistenceFailure("unable to read coverage ledger", error);
            } catch (RuntimeException error) {
                rollback(connection, error);
                throw error;
            }
        } catch (SQLException error) {
            throw persistenceFailure("unable to read coverage ledger", error);
        }
    }

    @Override
    public CoverageLedgerSnapshot compareAndSet(
            CoverageLedgerSnapshot expected,
            CoverageLedgerSnapshot replacement) {
        Objects.requireNonNull(expected, "expected");
        Objects.requireNonNull(replacement, "replacement");
        requireSameImmutableLedger(expected, replacement);
        requireSnapshotConsistent(replacement);

        try (Connection connection = dataSource.getConnection()) {
            begin(connection);
            try {
                StoredSnapshot stored = load(connection, expected.executionId(), true)
                        .orElseThrow(() -> new IllegalStateException(
                                "coverage ledger is not durably persisted"));
                if (stored.snapshot().equals(replacement)) {
                    connection.commit();
                    return stored.snapshot();
                }
                if (!stored.snapshot().equals(expected)) {
                    throw new IllegalStateException("stale coverage ledger snapshot");
                }

                replaceDispositions(connection, replacement);
                if (updateAnalysisState(
                        connection, replacement, stored.revision()) != 1) {
                    throw new IllegalStateException("stale coverage ledger revision");
                }

                StoredSnapshot persisted = load(
                        connection, replacement.executionId(), false)
                        .orElseThrow(() -> new IllegalStateException(
                                "coverage CAS returned no durable state"));
                if (!replacement.equals(persisted.snapshot())) {
                    throw new IllegalStateException(
                            "coverage CAS receipt does not match replacement state");
                }
                connection.commit();
                return persisted.snapshot();
            } catch (SQLException error) {
                rollback(connection, error);
                throw persistenceFailure("atomic coverage compare-and-set failed", error);
            } catch (RuntimeException error) {
                rollback(connection, error);
                throw error;
            }
        } catch (SQLException error) {
            throw persistenceFailure("unable to access coverage ledger storage", error);
        }
    }

    private static void begin(Connection connection) throws SQLException {
        connection.setTransactionIsolation(Connection.TRANSACTION_READ_COMMITTED);
        connection.setAutoCommit(false);
    }

    private static void beginRead(Connection connection) throws SQLException {
        connection.setReadOnly(true);
        connection.setTransactionIsolation(Connection.TRANSACTION_REPEATABLE_READ);
        connection.setAutoCommit(false);
    }

    private static void requireManifestIdentity(
            Connection connection,
            CoverageLedgerSeed seed) throws SQLException {
        try (PreparedStatement statement = connection.prepareStatement(
                SELECT_MANIFEST_IDENTITY)) {
            statement.setString(1, seed.executionId());
            statement.setString(2, seed.artifactManifestDigest());
            try (ResultSet result = statement.executeQuery()) {
                if (!result.next()) {
                    throw new IllegalArgumentException(
                            "coverage seed does not belong to a persisted manifest");
                }
                if (!seed.diffDigest().equals(result.getString("diff_digest"))
                        || seed.diffByteLength() != result.getLong("diff_byte_length")) {
                    throw new IllegalArgumentException(
                            "coverage seed diff identity conflicts with immutable manifest");
                }
            }
        }
    }

    private static int insertAnalysisState(
            Connection connection,
            CoverageLedgerSnapshot snapshot) throws SQLException {
        try (PreparedStatement statement = connection.prepareStatement(
                INSERT_ANALYSIS_STATE)) {
            int parameter = 1;
            statement.setString(parameter++, snapshot.executionId());
            statement.setInt(parameter++, snapshot.schemaVersion());
            statement.setString(parameter++, snapshot.artifactManifestDigest());
            statement.setString(parameter++, snapshot.diffDigest());
            statement.setLong(parameter++, snapshot.diffByteLength());
            statement.setString(parameter++, snapshot.ledgerDigest());
            statement.setString(parameter++, wire(snapshot.analysisState()));
            parameter = setCounts(statement, parameter, snapshot.counts());
            statement.setString(parameter, reasonCounts(snapshot));
            return statement.executeUpdate();
        }
    }

    private static void insertAnchor(
            Connection connection,
            CoverageLedgerSeed seed,
            CoverageAnchor anchor) throws SQLException {
        try (PreparedStatement statement = connection.prepareStatement(INSERT_ANCHOR)) {
            int parameter = 1;
            statement.setString(parameter++, anchor.anchorId());
            statement.setInt(parameter++, seed.schemaVersion());
            statement.setString(parameter++, seed.executionId());
            statement.setString(parameter++, seed.artifactManifestDigest());
            statement.setString(parameter++, seed.diffDigest());
            statement.setLong(parameter++, seed.diffByteLength());
            statement.setString(parameter++, seed.ledgerDigest());
            statement.setString(parameter++, anchor.sourceArtifactId());
            statement.setString(parameter++, anchor.sourceDigest());
            statement.setString(parameter++, anchor.parentHunkId());
            statement.setString(parameter++, anchor.changeId());
            statement.setString(parameter++, wire(anchor.changeStatus()));
            statement.setString(parameter++, wire(anchor.kind()));
            statement.setString(parameter++, anchor.oldPath());
            statement.setString(parameter++, anchor.newPath());
            statement.setInt(parameter++, anchor.oldStart());
            statement.setInt(parameter++, anchor.oldLineCount());
            statement.setInt(parameter++, anchor.newStart());
            statement.setInt(parameter++, anchor.newLineCount());
            statement.setBoolean(parameter++, anchor.mandatory());
            statement.setString(parameter++, wire(anchor.initialState()));
            statement.setString(parameter, anchor.reasonCode());
            if (statement.executeUpdate() != 1) {
                throw new SQLException("coverage anchor was not inserted atomically");
            }
        }
    }

    private static Optional<StoredSnapshot> load(
            Connection connection,
            String executionId,
            boolean forUpdate) throws SQLException {
        StateRow state = selectState(connection, executionId, forUpdate);
        if (state == null) {
            return Optional.empty();
        }
        List<CoverageAnchor> anchors = selectAnchors(connection, state);
        List<CoverageDisposition> dispositions = selectDispositions(
                connection, executionId);
        CoverageLedgerSnapshot snapshot = new CoverageLedgerSnapshot(
                state.schemaVersion(),
                state.executionId(),
                state.artifactManifestDigest(),
                state.diffDigest(),
                state.diffByteLength(),
                state.ledgerDigest(),
                anchors,
                dispositions,
                state.analysisState(),
                state.counts());
        requireSnapshotConsistent(snapshot);
        return Optional.of(new StoredSnapshot(snapshot, state.revision()));
    }

    private static StateRow selectState(
            Connection connection,
            String executionId,
            boolean forUpdate) throws SQLException {
        String sql = forUpdate ? SELECT_ANALYSIS_STATE + " FOR UPDATE" : SELECT_ANALYSIS_STATE;
        try (PreparedStatement statement = connection.prepareStatement(sql)) {
            statement.setString(1, executionId);
            try (ResultSet result = statement.executeQuery()) {
                if (!result.next()) {
                    return null;
                }
                return new StateRow(
                        result.getInt("schema_version"),
                        result.getString("execution_id"),
                        result.getString("artifact_manifest_digest"),
                        result.getString("diff_digest"),
                        result.getLong("diff_byte_length"),
                        result.getString("ledger_digest"),
                        analysisState(result.getString("analysis_state")),
                        new CoverageCounts(
                                result.getInt("inventory_anchor_count"),
                                result.getInt("pending_anchor_count"),
                                result.getInt("owner_pending_anchor_count"),
                                result.getInt("examined_anchor_count"),
                                result.getInt("incomplete_anchor_count"),
                                result.getInt("unsupported_anchor_count"),
                                result.getInt("failed_anchor_count"),
                                result.getInt("policy_excluded_anchor_count"),
                                result.getInt("deleted_recorded_anchor_count")),
                        result.getLong("revision"));
            }
        }
    }

    private static List<CoverageAnchor> selectAnchors(
            Connection connection,
            StateRow state) throws SQLException {
        List<CoverageAnchor> anchors = new ArrayList<>();
        try (PreparedStatement statement = connection.prepareStatement(SELECT_ANCHORS)) {
            statement.setString(1, state.executionId());
            try (ResultSet result = statement.executeQuery()) {
                while (result.next()) {
                    requireStoredIdentity(state, result);
                    anchors.add(new CoverageAnchor(
                            result.getString("anchor_id"),
                            result.getString("execution_id"),
                            result.getString("parent_hunk_id"),
                            result.getString("change_id"),
                            anchorKind(result.getString("anchor_kind")),
                            result.getString("old_path"),
                            result.getString("new_path"),
                            result.getInt("old_start"),
                            result.getInt("old_line_count"),
                            result.getInt("new_start"),
                            result.getInt("new_line_count"),
                            changeStatus(result.getString("change_status")),
                            result.getString("source_artifact_id"),
                            result.getString("source_digest"),
                            result.getBoolean("mandatory"),
                            anchorState(result.getString("initial_state")),
                            result.getString("initial_reason_code")));
                }
            }
        }
        return List.copyOf(anchors);
    }

    private static List<CoverageDisposition> selectDispositions(
            Connection connection,
            String executionId) throws SQLException {
        List<CoverageDisposition> dispositions = new ArrayList<>();
        try (PreparedStatement statement = connection.prepareStatement(
                SELECT_DISPOSITIONS)) {
            statement.setString(1, executionId);
            try (ResultSet result = statement.executeQuery()) {
                while (result.next()) {
                    dispositions.add(new CoverageDisposition(
                            result.getString("anchor_id"),
                            anchorState(result.getString("coverage_state")),
                            result.getString("reason_code")));
                }
            }
        }
        return List.copyOf(dispositions);
    }

    private static void replaceDispositions(
            Connection connection,
            CoverageLedgerSnapshot replacement) throws SQLException {
        try (PreparedStatement statement = connection.prepareStatement(
                DELETE_DISPOSITIONS)) {
            statement.setString(1, replacement.executionId());
            statement.executeUpdate();
        }
        for (CoverageDisposition disposition : replacement.dispositions()) {
            try (PreparedStatement statement = connection.prepareStatement(
                    INSERT_DISPOSITION)) {
                statement.setString(1, replacement.executionId());
                statement.setString(2, replacement.artifactManifestDigest());
                statement.setString(3, replacement.ledgerDigest());
                statement.setString(4, disposition.anchorId());
                statement.setString(5, wire(disposition.state()));
                statement.setString(6, disposition.reasonCode());
                if (statement.executeUpdate() != 1) {
                    throw new SQLException(
                            "coverage disposition was not inserted atomically");
                }
            }
        }
    }

    private static int updateAnalysisState(
            Connection connection,
            CoverageLedgerSnapshot replacement,
            long expectedRevision) throws SQLException {
        try (PreparedStatement statement = connection.prepareStatement(
                UPDATE_ANALYSIS_STATE)) {
            int parameter = 1;
            statement.setString(parameter++, wire(replacement.analysisState()));
            parameter = setCounts(statement, parameter, replacement.counts());
            statement.setString(parameter++, reasonCounts(replacement));
            statement.setString(parameter++, replacement.executionId());
            statement.setLong(parameter, expectedRevision);
            return statement.executeUpdate();
        }
    }

    private static int setCounts(
            PreparedStatement statement,
            int parameter,
            CoverageCounts counts) throws SQLException {
        statement.setInt(parameter++, counts.inventory());
        statement.setInt(parameter++, counts.pending());
        statement.setInt(parameter++, counts.ownerPending());
        statement.setInt(parameter++, counts.examined());
        statement.setInt(parameter++, counts.incomplete());
        statement.setInt(parameter++, counts.unsupported());
        statement.setInt(parameter++, counts.failed());
        statement.setInt(parameter++, counts.policyExcluded());
        statement.setInt(parameter++, counts.deletedRecorded());
        return parameter;
    }

    private static CoverageLedgerSnapshot initialSnapshot(CoverageLedgerSeed seed) {
        List<CoverageDisposition> dispositions = seed.anchors().stream()
                .map(anchor -> new CoverageDisposition(
                        anchor.anchorId(),
                        anchor.initialState(),
                        anchor.reasonCode()))
                .toList();
        CoverageCounts counts = counts(seed.anchors(), dispositions);
        return new CoverageLedgerSnapshot(
                seed.schemaVersion(),
                seed.executionId(),
                seed.artifactManifestDigest(),
                seed.diffDigest(),
                seed.diffByteLength(),
                seed.ledgerDigest(),
                seed.anchors(),
                dispositions,
                analysisState(seed.anchors(), dispositions),
                counts);
    }

    private static CoverageAnalysisState analysisState(
            List<CoverageAnchor> anchors,
            List<CoverageDisposition> dispositions) {
        Map<String, CoverageDisposition> byAnchor = dispositionMap(dispositions);
        List<CoverageAnchorState> mandatoryStates = new ArrayList<>();
        for (CoverageAnchor anchor : anchors) {
            CoverageDisposition disposition = byAnchor.get(anchor.anchorId());
            if (anchor.mandatory()) {
                mandatoryStates.add(disposition == null
                        ? anchor.initialState()
                        : disposition.state());
            }
        }
        if (mandatoryStates.isEmpty()) {
            return CoverageAnalysisState.EMPTY;
        }
        if (mandatoryStates.stream().anyMatch(state ->
                state == CoverageAnchorState.PENDING
                        || state == CoverageAnchorState.OWNER_PENDING)) {
            return CoverageAnalysisState.PENDING;
        }
        if (mandatoryStates.stream().allMatch(
                state -> state == CoverageAnchorState.EXAMINED)) {
            return CoverageAnalysisState.COMPLETE;
        }
        if (mandatoryStates.stream().noneMatch(
                state -> state == CoverageAnchorState.EXAMINED)
                && mandatoryStates.stream().anyMatch(
                        state -> state == CoverageAnchorState.FAILED)) {
            return CoverageAnalysisState.FAILED;
        }
        return CoverageAnalysisState.PARTIAL;
    }

    private static CoverageCounts counts(
            List<CoverageAnchor> anchors,
            List<CoverageDisposition> dispositions) {
        Map<String, CoverageDisposition> byAnchor = dispositionMap(dispositions);
        int pending = 0;
        int ownerPending = 0;
        int examined = 0;
        int incomplete = 0;
        int unsupported = 0;
        int failed = 0;
        int policyExcluded = 0;
        int deletedRecorded = 0;
        for (CoverageAnchor anchor : anchors) {
            CoverageDisposition disposition = byAnchor.remove(anchor.anchorId());
            CoverageAnchorState state = disposition == null
                    ? anchor.initialState()
                    : disposition.state();
            switch (state) {
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
        if (!byAnchor.isEmpty()) {
            throw new IllegalArgumentException(
                    "coverage dispositions reference unknown anchors");
        }
        return new CoverageCounts(
                anchors.size(),
                pending,
                ownerPending,
                examined,
                incomplete,
                unsupported,
                failed,
                policyExcluded,
                deletedRecorded);
    }

    private static Map<String, CoverageDisposition> dispositionMap(
            List<CoverageDisposition> dispositions) {
        Map<String, CoverageDisposition> byAnchor = new LinkedHashMap<>();
        String previous = null;
        for (CoverageDisposition disposition : dispositions) {
            Objects.requireNonNull(disposition, "coverage disposition");
            if (previous != null && previous.compareTo(disposition.anchorId()) >= 0) {
                throw new IllegalArgumentException(
                        "coverage dispositions must use canonical anchor order");
            }
            if (byAnchor.put(disposition.anchorId(), disposition) != null) {
                throw new IllegalArgumentException(
                        "coverage dispositions contain a duplicate anchor");
            }
            previous = disposition.anchorId();
        }
        return byAnchor;
    }

    private static void requireSnapshotConsistent(CoverageLedgerSnapshot snapshot) {
        requireCanonicalAnchors(new CoverageLedgerSeed(
                snapshot.schemaVersion(),
                snapshot.executionId(),
                snapshot.artifactManifestDigest(),
                snapshot.diffDigest(),
                snapshot.diffByteLength(),
                snapshot.ledgerDigest(),
                snapshot.anchors()));
        CoverageCounts calculated = counts(snapshot.anchors(), snapshot.dispositions());
        if (!calculated.equals(snapshot.counts())) {
            throw new IllegalArgumentException(
                    "coverage counts do not reconcile with durable anchors");
        }
        if (snapshot.analysisState() != CoverageAnalysisState.SUPERSEDED
                && snapshot.analysisState()
                != analysisState(snapshot.anchors(), snapshot.dispositions())) {
            throw new IllegalArgumentException(
                    "coverage analysis state does not reconcile with mandatory anchors");
        }
    }

    private static void requireCanonicalAnchors(CoverageLedgerSeed seed) {
        String previous = null;
        Set<String> ids = new java.util.HashSet<>();
        for (CoverageAnchor anchor : seed.anchors()) {
            Objects.requireNonNull(anchor, "coverage anchor");
            if (!seed.executionId().equals(anchor.executionId())) {
                throw new IllegalArgumentException(
                        "coverage anchor belongs to another execution");
            }
            if (previous != null && previous.compareTo(anchor.anchorId()) >= 0) {
                throw new IllegalArgumentException(
                        "coverage anchors must use canonical anchor order");
            }
            if (!ids.add(anchor.anchorId())) {
                throw new IllegalArgumentException(
                        "coverage anchors contain a duplicate identity");
            }
            previous = anchor.anchorId();
        }
    }

    private static void requireSeedReceipt(
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
                    "persisted coverage ledger conflicts with immutable seed");
        }
    }

    private static void requireSameImmutableLedger(
            CoverageLedgerSnapshot expected,
            CoverageLedgerSnapshot replacement) {
        boolean sameIdentity = expected.schemaVersion() == replacement.schemaVersion()
                && expected.executionId().equals(replacement.executionId())
                && expected.artifactManifestDigest().equals(
                        replacement.artifactManifestDigest())
                && expected.diffDigest().equals(replacement.diffDigest())
                && expected.diffByteLength() == replacement.diffByteLength()
                && expected.ledgerDigest().equals(replacement.ledgerDigest());
        if (!sameIdentity) {
            throw new IllegalArgumentException(
                    "coverage replacement identity conflicts with expected ledger");
        }
        if (!expected.anchors().equals(replacement.anchors())) {
            throw new IllegalArgumentException(
                    "coverage replacement changes immutable anchors");
        }
    }

    private static void requireStoredIdentity(
            StateRow state,
            ResultSet result) throws SQLException {
        if (state.schemaVersion() != result.getInt("schema_version")
                || !state.artifactManifestDigest().equals(
                        result.getString("artifact_manifest_digest"))
                || !state.diffDigest().equals(result.getString("diff_digest"))
                || state.diffByteLength() != result.getLong("diff_byte_length")
                || !state.ledgerDigest().equals(result.getString("ledger_digest"))) {
            throw new IllegalStateException(
                    "persisted coverage anchor conflicts with ledger identity");
        }
    }

    private static String reasonCounts(CoverageLedgerSnapshot snapshot) {
        Map<String, CoverageDisposition> dispositions = new HashMap<>();
        for (CoverageDisposition disposition : snapshot.dispositions()) {
            dispositions.put(disposition.anchorId(), disposition);
        }
        Map<String, Integer> reasons = new TreeMap<>();
        for (CoverageAnchor anchor : snapshot.anchors()) {
            CoverageDisposition disposition = dispositions.get(anchor.anchorId());
            String reason = disposition == null
                    ? anchor.reasonCode()
                    : disposition.reasonCode();
            if (reason != null) {
                reasons.merge(reason, 1, Integer::sum);
            }
        }
        try {
            return JSON.writeValueAsString(reasons);
        } catch (JsonProcessingException error) {
            throw new IllegalStateException(
                    "coverage reason counts are not serializable", error);
        }
    }

    private static String wire(Enum<?> value) {
        return value.name().toLowerCase(Locale.ROOT).replace('_', '-');
    }

    private static CoverageAnchorState anchorState(String value) {
        return CoverageAnchorState.valueOf(enumName(value));
    }

    private static CoverageAnchorKind anchorKind(String value) {
        return CoverageAnchorKind.valueOf(enumName(value));
    }

    private static CoverageAnalysisState analysisState(String value) {
        return CoverageAnalysisState.valueOf(enumName(value));
    }

    private static ExactDiffInventory.ChangeStatus changeStatus(String value) {
        return ExactDiffInventory.ChangeStatus.valueOf(enumName(value));
    }

    private static String enumName(String value) {
        return value.toUpperCase(Locale.ROOT).replace('-', '_');
    }

    private static void requireExecutionId(String executionId) {
        if (executionId == null || executionId.isBlank()) {
            throw new IllegalArgumentException("executionId must not be blank");
        }
    }

    private static void rollback(Connection connection, Throwable original) {
        try {
            connection.rollback();
        } catch (SQLException rollbackFailure) {
            original.addSuppressed(rollbackFailure);
        }
    }

    private static IllegalStateException persistenceFailure(
            String message,
            SQLException error) {
        return new IllegalStateException(message, error);
    }

    private record StoredSnapshot(CoverageLedgerSnapshot snapshot, long revision) {
    }

    private record StateRow(
            int schemaVersion,
            String executionId,
            String artifactManifestDigest,
            String diffDigest,
            long diffByteLength,
            String ledgerDigest,
            CoverageAnalysisState analysisState,
            CoverageCounts counts,
            long revision) {
    }
}
