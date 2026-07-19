package org.rostilos.codecrow.pipelineagent;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.List;

import javax.sql.DataSource;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.analysisengine.coverage.CoverageAnalysisState;
import org.rostilos.codecrow.analysisengine.coverage.CoverageAnchor;
import org.rostilos.codecrow.analysisengine.coverage.CoverageAnchorKind;
import org.rostilos.codecrow.analysisengine.coverage.CoverageAnchorState;
import org.rostilos.codecrow.analysisengine.coverage.CoverageCounts;
import org.rostilos.codecrow.analysisengine.coverage.CoverageDisposition;
import org.rostilos.codecrow.analysisengine.coverage.CoverageLedgerPersistencePort;
import org.rostilos.codecrow.analysisengine.coverage.CoverageLedgerSeed;
import org.rostilos.codecrow.analysisengine.coverage.CoverageLedgerSnapshot;
import org.rostilos.codecrow.analysisengine.execution.ExecutionManifestService;
import org.rostilos.codecrow.analysisengine.execution.ImmutableExecutionManifest;
import org.rostilos.codecrow.pipelineagent.execution.persistence.PostgresCoverageLedgerPersistenceAdapter;
import org.rostilos.codecrow.pipelineagent.execution.persistence.PostgresExecutionManifestPersistenceAdapter;
import org.rostilos.codecrow.vcsclient.diff.ExactDiffInventory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.core.JdbcTemplate;

/** PostgreSQL restart and CAS contract for the VS-05 durable coverage ledger. */
class CoverageLedgerPersistenceIT extends BasePipelineAgentIT {

    private static final int SCHEMA_VERSION = 1;
    private static final String EXECUTION_ID = "pr:coverage-ledger-postgres-0001";
    private static final String DIFF_ARTIFACT_ID = "diff:coverage-ledger-postgres-0001";
    private static final String LEDGER_DIGEST = "9".repeat(64);
    private static final String RAW_DIFF_TEXT = """
            diff --git a/src/App.java b/src/App.java
            --- a/src/App.java
            +++ b/src/App.java
            @@ -1 +1 @@
            -old
            +new
            diff --git a/assets/logo.bin b/assets/logo.bin
            new file mode 100644
            Binary files /dev/null and b/assets/logo.bin differ
            """;
    private static final byte[] RAW_DIFF = RAW_DIFF_TEXT.getBytes(StandardCharsets.UTF_8);
    private static final String DIFF_DIGEST = sha256(RAW_DIFF);

    @Autowired private DataSource dataSource;
    @Autowired private JdbcTemplate jdbc;

    @BeforeEach
    void prepareCoverageLedgerSchema() throws IOException {
        applyManagedMigrationWhenTableMissing(
                "review_execution",
                "db/migration/managed/V2.15.0__immutable_execution_manifest.sql",
                true);
        applyManagedMigrationWhenTableMissing(
                "review_coverage_anchor",
                "db/migration/managed/V2.16.0__coverage_ledger.sql",
                false);
    }

    @Test
    void exactCreateRetryAndFreshAdapterRestartReloadOneCanonicalLedger() {
        long projectId = createTestProject(
                "vs05-ledger-restart", "codecrow", "coverage-ledger-restart");
        ImmutableExecutionManifest manifest = persistManifest(projectId);
        CoverageLedgerSeed seed = seed(manifest);
        CoverageLedgerSnapshot expected = initialSnapshot(seed);

        CoverageLedgerSnapshot created = newStore().createOrLoad(seed);
        CoverageLedgerSnapshot exactRetry = newStore().createOrLoad(seed);
        CoverageLedgerSnapshot restarted = newStore()
                .findByExecutionId(EXECUTION_ID)
                .orElseThrow();

        assertThat(created).isEqualTo(expected);
        assertThat(exactRetry).isEqualTo(expected);
        assertThat(restarted).isEqualTo(expected);
        assertThat(restarted.anchors())
                .extracting(CoverageAnchor::anchorId)
                .containsExactly("1".repeat(64), "2".repeat(64));
        assertThat(rowCount("review_coverage_anchor", EXECUTION_ID)).isEqualTo(2);
        assertThat(rowCount("review_coverage_disposition", EXECUTION_ID)).isEqualTo(2);
        assertThat(rowCount("review_analysis_state", EXECUTION_ID)).isOne();
    }

    @Test
    void compareAndSetIsAtomicAndRejectsStaleOrForeignLedgerReceipts() {
        long projectId = createTestProject(
                "vs05-ledger-cas", "codecrow", "coverage-ledger-cas");
        ImmutableExecutionManifest manifest = persistManifest(projectId);
        CoverageLedgerSeed seed = seed(manifest);
        CoverageLedgerSnapshot initial = newStore().createOrLoad(seed);
        CoverageLedgerSnapshot complete = completeSnapshot(seed);

        assertThat(newStore().compareAndSet(initial, complete)).isEqualTo(complete);
        assertThat(newStore().findByExecutionId(EXECUTION_ID)).contains(complete);
        assertThat(rowCount("review_coverage_disposition", EXECUTION_ID)).isEqualTo(2);

        CoverageLedgerSnapshot conflictingTerminal = failedSnapshot(seed);
        assertThatThrownBy(() -> newStore().compareAndSet(initial, conflictingTerminal))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("stale");

        assertThatThrownBy(() -> newStore().compareAndSet(
                complete,
                copyIdentity(
                        complete,
                        "pr:coverage-ledger-postgres-foreign",
                        "8".repeat(64),
                        "7".repeat(64))))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("identity");

        assertThat(newStore().findByExecutionId(EXECUTION_ID)).contains(complete);
        assertThat(rowCount("review_coverage_disposition", EXECUTION_ID)).isEqualTo(2);
    }

    @Test
    void createOrLoadRejectsAConflictingManifestOrLedgerWithoutPartialWrites() {
        long projectId = createTestProject(
                "vs05-ledger-conflict", "codecrow", "coverage-ledger-conflict");
        ImmutableExecutionManifest manifest = persistManifest(projectId);
        CoverageLedgerSeed original = seed(manifest);
        CoverageLedgerSnapshot expected = newStore().createOrLoad(original);

        assertThatThrownBy(() -> newStore().createOrLoad(new CoverageLedgerSeed(
                original.schemaVersion(),
                original.executionId(),
                "8".repeat(64),
                original.diffDigest(),
                original.diffByteLength(),
                original.ledgerDigest(),
                original.anchors())))
                .isInstanceOf(RuntimeException.class);
        assertThatThrownBy(() -> newStore().createOrLoad(new CoverageLedgerSeed(
                original.schemaVersion(),
                original.executionId(),
                original.artifactManifestDigest(),
                original.diffDigest(),
                original.diffByteLength(),
                "7".repeat(64),
                original.anchors())))
                .isInstanceOf(RuntimeException.class);

        assertThat(newStore().findByExecutionId(EXECUTION_ID)).contains(expected);
        assertThat(rowCount("review_coverage_anchor", EXECUTION_ID)).isEqualTo(2);
        assertThat(rowCount("review_analysis_state", EXECUTION_ID)).isOne();
    }

    private CoverageLedgerPersistencePort newStore() {
        return new PostgresCoverageLedgerPersistenceAdapter(dataSource);
    }

    private ImmutableExecutionManifest persistManifest(long projectId) {
        ImmutableExecutionManifest manifest = ImmutableExecutionManifest.create(
                1,
                EXECUTION_ID,
                projectId,
                "github:codecrow/coverage-ledger",
                82L,
                "a".repeat(40),
                "b".repeat(40),
                "c".repeat(40),
                DIFF_ARTIFACT_ID,
                DIFF_DIGEST,
                RAW_DIFF.length,
                ImmutableExecutionManifest.RAW_DIFF_ARTIFACT_KIND,
                "java-vcs-acquisition",
                "analysis-engine-v1",
                ImmutableExecutionManifest.CURRENT_ARTIFACT_SCHEMA_VERSION,
                "candidate-review-v2",
                "creation:coverage-ledger-postgres",
                Instant.parse("2026-07-16T08:00:00Z"));
        return new ExecutionManifestService(
                new PostgresExecutionManifestPersistenceAdapter(dataSource))
                .persistBeforeWork(manifest, RAW_DIFF.clone());
    }

    private static CoverageLedgerSeed seed(ImmutableExecutionManifest manifest) {
        List<CoverageAnchor> anchors = List.of(
                anchor(
                        "1".repeat(64),
                        "3".repeat(64),
                        "5".repeat(64),
                        CoverageAnchorKind.TEXT_HUNK,
                        "src/App.java",
                        "src/App.java",
                        1,
                        1,
                        1,
                        1,
                        ExactDiffInventory.ChangeStatus.MODIFY,
                        true,
                        CoverageAnchorState.PENDING,
                        null),
                anchor(
                        "2".repeat(64),
                        "4".repeat(64),
                        "6".repeat(64),
                        CoverageAnchorKind.FILE_CHANGE,
                        null,
                        "assets/logo.bin",
                        0,
                        0,
                        0,
                        0,
                        ExactDiffInventory.ChangeStatus.ADD,
                        true,
                        CoverageAnchorState.UNSUPPORTED,
                        "binary_change"));
        return new CoverageLedgerSeed(
                SCHEMA_VERSION,
                EXECUTION_ID,
                manifest.artifactManifestDigest(),
                DIFF_DIGEST,
                RAW_DIFF.length,
                LEDGER_DIGEST,
                anchors);
    }

    private static CoverageAnchor anchor(
            String anchorId,
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
            boolean mandatory,
            CoverageAnchorState state,
            String reasonCode) {
        return new CoverageAnchor(
                anchorId,
                EXECUTION_ID,
                parentHunkId,
                changeId,
                kind,
                oldPath,
                newPath,
                oldStart,
                oldLineCount,
                newStart,
                newLineCount,
                changeStatus,
                DIFF_ARTIFACT_ID,
                DIFF_DIGEST,
                mandatory,
                state,
                reasonCode);
    }

    private static CoverageLedgerSnapshot initialSnapshot(CoverageLedgerSeed seed) {
        return snapshot(
                seed,
                initialDispositions(seed),
                CoverageAnalysisState.PENDING,
                new CoverageCounts(2, 1, 0, 0, 0, 1, 0, 0, 0));
    }

    private static CoverageLedgerSnapshot completeSnapshot(CoverageLedgerSeed seed) {
        return snapshot(
                seed,
                List.of(
                        new CoverageDisposition(
                                "1".repeat(64), CoverageAnchorState.EXAMINED, null),
                        new CoverageDisposition(
                                "2".repeat(64),
                                CoverageAnchorState.UNSUPPORTED,
                                "binary_change")),
                CoverageAnalysisState.COMPLETE,
                new CoverageCounts(2, 0, 0, 1, 0, 1, 0, 0, 0));
    }

    private static CoverageLedgerSnapshot failedSnapshot(CoverageLedgerSeed seed) {
        return snapshot(
                seed,
                List.of(
                        new CoverageDisposition(
                                "1".repeat(64),
                                CoverageAnchorState.FAILED,
                                "model_failed"),
                        new CoverageDisposition(
                                "2".repeat(64),
                                CoverageAnchorState.UNSUPPORTED,
                                "binary_change")),
                CoverageAnalysisState.PARTIAL,
                new CoverageCounts(2, 0, 0, 0, 0, 1, 1, 0, 0));
    }

    private static List<CoverageDisposition> initialDispositions(
            CoverageLedgerSeed seed) {
        return seed.anchors().stream()
                .map(anchor -> new CoverageDisposition(
                        anchor.anchorId(),
                        anchor.initialState(),
                        anchor.reasonCode()))
                .toList();
    }

    private static CoverageLedgerSnapshot snapshot(
            CoverageLedgerSeed seed,
            List<CoverageDisposition> dispositions,
            CoverageAnalysisState state,
            CoverageCounts counts) {
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

    private static CoverageLedgerSnapshot copyIdentity(
            CoverageLedgerSnapshot source,
            String executionId,
            String manifestDigest,
            String ledgerDigest) {
        return new CoverageLedgerSnapshot(
                source.schemaVersion(),
                executionId,
                manifestDigest,
                source.diffDigest(),
                source.diffByteLength(),
                ledgerDigest,
                source.anchors(),
                source.dispositions(),
                source.analysisState(),
                source.counts());
    }

    private int rowCount(String table, String executionId) {
        Integer count = jdbc.queryForObject(
                "SELECT COUNT(*) FROM " + table + " WHERE execution_id = ?",
                Integer.class,
                executionId);
        return count == null ? 0 : count;
    }

    private void applyManagedMigrationWhenTableMissing(
            String table,
            String migrationResource,
            boolean removeHibernateCandidateColumns) throws IOException {
        Boolean exists = jdbc.queryForObject(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                        + "WHERE table_schema = current_schema() AND table_name = ?)",
                Boolean.class,
                table);
        if (Boolean.TRUE.equals(exists)) {
            return;
        }
        if (removeHibernateCandidateColumns) {
            jdbc.execute("ALTER TABLE code_analysis "
                    + "DROP COLUMN IF EXISTS execution_id CASCADE");
            jdbc.execute("ALTER TABLE code_analysis "
                    + "DROP COLUMN IF EXISTS artifact_manifest_digest CASCADE");
        }
        String migration = new ClassPathResource(migrationResource)
                .getContentAsString(StandardCharsets.UTF_8);
        jdbc.execute(migration);
    }

    private static String sha256(byte[] value) {
        try {
            return java.util.HexFormat.of().formatHex(
                    MessageDigest.getInstance("SHA-256").digest(value));
        } catch (NoSuchAlgorithmException error) {
            throw new AssertionError("SHA-256 must exist in the test runtime", error);
        }
    }
}
