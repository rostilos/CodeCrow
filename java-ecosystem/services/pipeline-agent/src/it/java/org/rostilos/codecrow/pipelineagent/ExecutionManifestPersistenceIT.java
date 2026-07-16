package org.rostilos.codecrow.pipelineagent;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.analysisengine.execution.ArtifactManifestEntry;
import org.rostilos.codecrow.analysisengine.execution.ExecutionArtifactPayload;
import org.rostilos.codecrow.analysisengine.execution.ExecutionManifestPersistencePort;
import org.rostilos.codecrow.analysisengine.execution.ExecutionManifestService;
import org.rostilos.codecrow.analysisengine.execution.ImmutableExecutionManifest;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.pipelineagent.execution.persistence.PostgresExecutionManifestPersistenceAdapter;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.core.io.ClassPathResource;
import org.springframework.dao.DataAccessException;
import org.springframework.jdbc.core.JdbcTemplate;

import javax.sql.DataSource;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Callable;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/** PostgreSQL contract for the durable P1-01 manifest boundary. */
class ExecutionManifestPersistenceIT extends BasePipelineAgentIT {
    private static final byte[] RAW_DIFF = ("diff --git a/a.java b/a.java\n"
            + "--- a/a.java\n"
            + "+++ b/a.java\n"
            + "@@ -1 +1 @@\n"
            + "-old\n"
            + "+new\n").getBytes(StandardCharsets.UTF_8);
    private static final String DIFF_DIGEST = sha256(RAW_DIFF);
    private static final String EXECUTION_ID = "pr:execution-postgres-0001";
    private static final String DIFF_ARTIFACT_ID = "diff:postgres-pull-request-82";

    @Autowired private DataSource dataSource;
    @Autowired private JdbcTemplate jdbc;
    @Autowired private CodeAnalysisService codeAnalysisService;

    @BeforeEach
    void prepareExecutionManifestSchema() throws IOException {
        Boolean migrationApplied = jdbc.queryForObject(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                        + "WHERE table_schema = current_schema() "
                        + "AND table_name = 'review_execution')",
                Boolean.class);
        if (!Boolean.TRUE.equals(migrationApplied)) {
            // The IT profile lets Hibernate create the current entity model.
            // Reconstruct the pre-V2.15 output table shape so this test proves
            // the real additive migration instead of starting with its new
            // columns already synthesized by Hibernate.
            jdbc.execute("ALTER TABLE code_analysis "
                    + "DROP COLUMN IF EXISTS execution_id CASCADE");
            jdbc.execute("ALTER TABLE code_analysis "
                    + "DROP COLUMN IF EXISTS artifact_manifest_digest CASCADE");
            String migration = new ClassPathResource(
                    "db/migration/managed/V2.15.0__immutable_execution_manifest.sql")
                    .getContentAsString(StandardCharsets.UTF_8);
            jdbc.execute(migration);
            return;
        }

        // V2.15 also adds output-binding columns/triggers and widens SHA
        // coordinates, so replaying the complete non-repeatable migration per
        // method is invalid. Clear only task-owned rows between cases.
        jdbc.update("DELETE FROM code_analysis WHERE execution_id IS NOT NULL");
        jdbc.update("DELETE FROM review_execution");
    }

    @Test
    void concurrentExactCreateOrLoadIsAtomicAndIdempotent() throws Exception {
        long projectId = createTestProject("p1-01-atomic", "codecrow", "manifest-atomic");
        ImmutableExecutionManifest manifest = manifest(
                EXECUTION_ID,
                projectId,
                DIFF_ARTIFACT_ID,
                "b".repeat(40),
                "creation:postgres-0001");
        CountDownLatch ready = new CountDownLatch(2);
        CountDownLatch start = new CountDownLatch(1);
        ExecutorService executor = Executors.newFixedThreadPool(2);

        Callable<ImmutableExecutionManifest> createOrLoad = () -> {
            ready.countDown();
            if (!start.await(10, TimeUnit.SECONDS)) {
                throw new IllegalStateException("concurrent create-or-load start timed out");
            }
            return new ExecutionManifestService(newStore())
                    .persistBeforeWork(manifest, RAW_DIFF.clone());
        };

        try {
            Future<ImmutableExecutionManifest> first = executor.submit(createOrLoad);
            Future<ImmutableExecutionManifest> second = executor.submit(createOrLoad);
            assertThat(ready.await(10, TimeUnit.SECONDS)).isTrue();
            start.countDown();

            assertThat(List.of(
                    first.get(10, TimeUnit.SECONDS),
                    second.get(10, TimeUnit.SECONDS)))
                    .containsExactly(manifest, manifest);
        } finally {
            executor.shutdownNow();
            assertThat(executor.awaitTermination(10, TimeUnit.SECONDS)).isTrue();
        }

        assertThat(rowCount("review_execution", "id", EXECUTION_ID)).isOne();
        assertThat(rowCount("review_artifact", "id", DIFF_ARTIFACT_ID)).isOne();
        assertThat(rowCount("review_artifact", "execution_id", EXECUTION_ID)).isOne();
    }

    @Test
    void restartAndCandidateDisablementPreserveTheDurableManifest() {
        long projectId = createTestProject("p1-01-restart", "codecrow", "manifest-restart");
        ImmutableExecutionManifest manifest = manifest(
                EXECUTION_ID,
                projectId,
                DIFF_ARTIFACT_ID,
                "b".repeat(40),
                "creation:postgres-0001");
        new ExecutionManifestService(newStore()).persistBeforeWork(manifest, RAW_DIFF.clone());

        ExecutionManifestService restarted = new ExecutionManifestService(newStore());

        assertThat(restarted.requireVerified(EXECUTION_ID)).isEqualTo(manifest);
    }

    @Test
    void reconstructsTheCompleteManifestInventoryAndExactArtifactBytes() {
        long projectId = createTestProject(
                "p1-01-artifacts",
                "codecrow",
                "manifest-artifacts");
        String headSha = "b".repeat(40);
        byte[] sourceContent = "class A {}\n".getBytes(StandardCharsets.UTF_8);
        ArtifactManifestEntry rawDiff = artifact(
                EXECUTION_ID,
                DIFF_ARTIFACT_ID,
                ImmutableExecutionManifest.RAW_DIFF_CONTENT_KEY,
                headSha,
                DIFF_DIGEST,
                RAW_DIFF.length,
                ArtifactManifestEntry.Kind.RAW_DIFF);
        ArtifactManifestEntry sourceFile = artifact(
                EXECUTION_ID,
                "source:postgres-file-a",
                "src/main/java/A.java",
                headSha,
                sha256(sourceContent),
                sourceContent.length,
                ArtifactManifestEntry.Kind.SOURCE_FILE);
        ImmutableExecutionManifest manifest = manifest(
                EXECUTION_ID,
                projectId,
                DIFF_ARTIFACT_ID,
                headSha,
                "creation:postgres-0001",
                List.of(sourceFile, rawDiff));
        List<ExecutionArtifactPayload> inputArtifacts = List.of(
                new ExecutionArtifactPayload(rawDiff, RAW_DIFF),
                new ExecutionArtifactPayload(sourceFile, sourceContent));

        new ExecutionManifestService(newStore())
                .persistBeforeWork(manifest, inputArtifacts);

        ExecutionManifestPersistencePort.PersistedExecution reloaded = newStore()
                .findByExecutionId(EXECUTION_ID)
                .orElseThrow();
        assertThat(reloaded.manifest()).isEqualTo(manifest);
        assertThat(reloaded.inputArtifacts()).containsExactlyElementsOf(inputArtifacts);
        assertThat(rowCount("review_artifact", "execution_id", EXECUTION_ID))
                .isEqualTo(2);
    }

    @Test
    void conflictingIdentityAndCrossExecutionArtifactReuseFailWithoutPartialWrites() {
        long projectId = createTestProject("p1-01-owner", "codecrow", "manifest-owner");
        ImmutableExecutionManifest original = manifest(
                EXECUTION_ID,
                projectId,
                DIFF_ARTIFACT_ID,
                "b".repeat(40),
                "creation:postgres-0001");
        new ExecutionManifestService(newStore()).persistBeforeWork(original, RAW_DIFF.clone());

        ImmutableExecutionManifest conflictingIdentity = manifest(
                EXECUTION_ID,
                projectId,
                DIFF_ARTIFACT_ID,
                "d".repeat(40),
                "creation:postgres-0002");
        assertThatThrownBy(() -> new ExecutionManifestService(newStore())
                .persistBeforeWork(conflictingIdentity, RAW_DIFF.clone()))
                .isInstanceOf(RuntimeException.class);

        String foreignExecutionId = "pr:execution-postgres-0002";
        ImmutableExecutionManifest crossExecutionReuse = manifest(
                foreignExecutionId,
                projectId,
                DIFF_ARTIFACT_ID,
                "e".repeat(40),
                "creation:postgres-0003");
        assertThatThrownBy(() -> new ExecutionManifestService(newStore())
                .persistBeforeWork(crossExecutionReuse, RAW_DIFF.clone()))
                .isInstanceOf(RuntimeException.class);

        assertThat(new ExecutionManifestService(newStore()).requireVerified(EXECUTION_ID))
                .isEqualTo(original);
        assertThat(rowCount("review_execution", "id", EXECUTION_ID)).isOne();
        assertThat(rowCount("review_execution", "id", foreignExecutionId)).isZero();
        assertThat(rowCount("review_artifact", "id", DIFF_ARTIFACT_ID)).isOne();
    }

    @Test
    void immutableRowsRejectUpdatesAndRestartVerificationRejectsOwnerLevelTampering() {
        long projectId = createTestProject("p1-01-tamper", "codecrow", "manifest-tamper");
        ImmutableExecutionManifest manifest = manifest(
                EXECUTION_ID,
                projectId,
                DIFF_ARTIFACT_ID,
                "b".repeat(40),
                "creation:postgres-0001");
        new ExecutionManifestService(newStore()).persistBeforeWork(manifest, RAW_DIFF.clone());

        assertThatThrownBy(() -> jdbc.update(
                "UPDATE review_execution SET head_sha = ? WHERE id = ?",
                "d".repeat(40),
                EXECUTION_ID))
                .isInstanceOf(DataAccessException.class);
        assertThatThrownBy(() -> jdbc.update(
                "UPDATE review_artifact SET content_digest = ? WHERE id = ?",
                "0".repeat(64),
                DIFF_ARTIFACT_ID))
                .isInstanceOf(DataAccessException.class);
        assertThat(new ExecutionManifestService(newStore()).requireVerified(EXECUTION_ID))
                .isEqualTo(manifest);

        jdbc.execute("ALTER TABLE review_artifact "
                + "DISABLE TRIGGER review_artifact_immutable_update");
        try {
            int altered = jdbc.update(
                    "UPDATE review_artifact SET content_digest = ? "
                            + "WHERE id = ? AND execution_id = ?",
                    "0".repeat(64),
                    DIFF_ARTIFACT_ID,
                    EXECUTION_ID);
            assertThat(altered).isOne();
        } finally {
            jdbc.execute("ALTER TABLE review_artifact "
                    + "ENABLE TRIGGER review_artifact_immutable_update");
        }

        assertThatThrownBy(() -> new ExecutionManifestService(newStore())
                .requireVerified(EXECUTION_ID))
                .isInstanceOf(RuntimeException.class);
    }

    @Test
    void candidateOutputRequiresExactRelationalBindingAndWriteOnceIdentity() {
        long projectId = createTestProject(
                "p1-01-output-binding",
                "codecrow",
                "manifest-output-binding");
        String headSha = "b".repeat(64);
        ImmutableExecutionManifest manifest = manifest(
                EXECUTION_ID,
                projectId,
                DIFF_ARTIFACT_ID,
                headSha,
                "creation:postgres-output-binding");
        new ExecutionManifestService(newStore())
                .persistBeforeWork(manifest, RAW_DIFF.clone());

        assertThatThrownBy(() -> insertCodeAnalysis(
                projectId,
                82L,
                headSha,
                EXECUTION_ID,
                null))
                .isInstanceOf(DataAccessException.class);
        assertThatThrownBy(() -> insertCodeAnalysis(
                projectId,
                82L,
                headSha,
                EXECUTION_ID,
                "0".repeat(64)))
                .isInstanceOf(DataAccessException.class);

        assertThat(insertCodeAnalysis(
                projectId,
                82L,
                headSha,
                EXECUTION_ID,
                manifest.artifactManifestDigest()))
                .isOne();
        assertThat(rowCount("code_analysis", "execution_id", EXECUTION_ID))
                .isOne();

        assertThatThrownBy(() -> jdbc.update(
                "UPDATE code_analysis SET artifact_manifest_digest = ? "
                        + "WHERE execution_id = ?",
                "1".repeat(64),
                EXECUTION_ID))
                .isInstanceOf(DataAccessException.class);
        assertThatThrownBy(() -> insertCodeAnalysis(
                projectId,
                83L,
                headSha,
                EXECUTION_ID,
                manifest.artifactManifestDigest()))
                .isInstanceOf(DataAccessException.class);
    }

    @Test
    void concurrentCandidateOutputRetryReloadsTheSingleManifestBoundWinner()
            throws Exception {
        long projectId = createTestProject(
                "p1-01-output-race", "codecrow", "manifest-output-race");
        String headSha = "b".repeat(64);
        ImmutableExecutionManifest manifest = manifest(
                EXECUTION_ID,
                projectId,
                DIFF_ARTIFACT_ID,
                headSha,
                "creation:postgres-output-race");
        new ExecutionManifestService(newStore())
                .persistBeforeWork(manifest, RAW_DIFF.clone());

        CountDownLatch ready = new CountDownLatch(2);
        CountDownLatch start = new CountDownLatch(1);
        ExecutorService executor = Executors.newFixedThreadPool(2);
        Callable<CodeAnalysis> createOrReload = () -> {
            Project project = projectRepository.findById(projectId).orElseThrow();
            ready.countDown();
            if (!start.await(10, TimeUnit.SECONDS)) {
                throw new IllegalStateException("candidate output race start timed out");
            }
            return codeAnalysisService.createCandidateAnalysisFromAiResponse(
                    project,
                    Map.of("comment", "review"),
                    82L,
                    "main",
                    "feature",
                    headSha,
                    "author-id",
                    "author",
                    "f".repeat(64),
                    Map.of(),
                    null,
                    null,
                    EXECUTION_ID,
                    manifest.artifactManifestDigest());
        };

        try {
            Future<CodeAnalysis> first = executor.submit(createOrReload);
            Future<CodeAnalysis> second = executor.submit(createOrReload);
            assertThat(ready.await(10, TimeUnit.SECONDS)).isTrue();
            start.countDown();

            assertThat(List.of(
                    first.get(10, TimeUnit.SECONDS).getExecutionId(),
                    second.get(10, TimeUnit.SECONDS).getExecutionId()))
                    .containsOnly(EXECUTION_ID);
        } finally {
            executor.shutdownNow();
            assertThat(executor.awaitTermination(10, TimeUnit.SECONDS)).isTrue();
        }

        assertThat(rowCount("code_analysis", "execution_id", EXECUTION_ID)).isOne();
    }

    private ExecutionManifestPersistencePort newStore() {
        return new PostgresExecutionManifestPersistenceAdapter(dataSource);
    }

    private int rowCount(String table, String column, String value) {
        Integer count = jdbc.queryForObject(
                "SELECT COUNT(*) FROM " + table + " WHERE " + column + " = ?",
                Integer.class,
                value);
        return count == null ? 0 : count;
    }

    private int insertCodeAnalysis(
            long projectId,
            long pullRequestId,
            String commitHash,
            String executionId,
            String manifestDigest) {
        return jdbc.update("""
                INSERT INTO code_analysis (
                    project_id,
                    analysis_type,
                    pr_number,
                    commit_hash,
                    execution_id,
                    artifact_manifest_digest,
                    status,
                    total_issues,
                    high_severity_count,
                    medium_severity_count,
                    low_severity_count,
                    info_severity_count,
                    resolved_count,
                    created_at,
                    updated_at
                ) VALUES (?, 'PR_REVIEW', ?, ?, ?, ?, 'ACCEPTED', 0, 0, 0, 0, 0, 0,
                          CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                projectId,
                pullRequestId,
                commitHash,
                executionId,
                manifestDigest);
    }

    private static ImmutableExecutionManifest manifest(
            String executionId,
            long projectId,
            String artifactId,
            String headSha,
            String creationFence) {
        return ImmutableExecutionManifest.create(
                1,
                executionId,
                projectId,
                "github:codecrow/codecrow-public",
                82L,
                "a".repeat(40),
                headSha,
                "c".repeat(40),
                artifactId,
                DIFF_DIGEST,
                RAW_DIFF.length,
                "raw-diff",
                "java-vcs-acquisition",
                "analysis-engine-v1",
                "review-artifact-v1",
                "candidate-review-v2",
                creationFence,
                Instant.parse("2026-07-15T12:00:00Z"));
    }

    private static ImmutableExecutionManifest manifest(
            String executionId,
            long projectId,
            String artifactId,
            String headSha,
            String creationFence,
            List<ArtifactManifestEntry> inputArtifacts) {
        return ImmutableExecutionManifest.create(
                1,
                executionId,
                projectId,
                "github:codecrow/codecrow-public",
                82L,
                "a".repeat(40),
                headSha,
                "c".repeat(40),
                artifactId,
                DIFF_DIGEST,
                RAW_DIFF.length,
                "raw-diff",
                "java-vcs-acquisition",
                "analysis-engine-v1",
                "review-artifact-v1",
                "candidate-review-v2",
                creationFence,
                Instant.parse("2026-07-15T12:00:00Z"),
                inputArtifacts);
    }

    private static ArtifactManifestEntry artifact(
            String executionId,
            String artifactId,
            String contentKey,
            String snapshotSha,
            String contentDigest,
            long byteLength,
            ArtifactManifestEntry.Kind kind) {
        return new ArtifactManifestEntry(
                executionId,
                artifactId,
                contentKey,
                snapshotSha,
                contentDigest,
                byteLength,
                kind,
                "review-artifact-v1",
                "java-vcs-acquisition",
                "analysis-engine-v1");
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
