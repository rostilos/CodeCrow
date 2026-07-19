package org.rostilos.codecrow.pipelineagent.execution.persistence;

import org.rostilos.codecrow.analysisengine.execution.ArtifactManifestEntry;
import org.rostilos.codecrow.analysisengine.execution.ExecutionArtifactPayload;
import org.rostilos.codecrow.analysisengine.execution.ExecutionManifestPersistencePort;
import org.rostilos.codecrow.analysisengine.execution.ImmutableExecutionManifest;
import org.springframework.stereotype.Repository;

import javax.sql.DataSource;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Objects;
import java.util.Optional;

/** PostgreSQL-backed immutable execution-manifest store. */
@Repository
public class PostgresExecutionManifestPersistenceAdapter
        implements ExecutionManifestPersistencePort {
    private static final String INSERT_EXECUTION = """
            INSERT INTO review_execution (
                id,
                schema_version,
                project_id,
                repository_id,
                pull_request_id,
                base_sha,
                head_sha,
                merge_base_sha,
                diff_artifact_id,
                diff_digest,
                diff_byte_length,
                diff_artifact_kind,
                diff_artifact_producer,
                diff_artifact_producer_version,
                artifact_schema_version,
                policy_version,
                creation_fence,
                created_at,
                artifact_manifest_digest
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (id) DO NOTHING
            """;

    private static final String INSERT_ARTIFACT = """
            INSERT INTO review_artifact (
                id,
                execution_id,
                artifact_manifest_digest,
                kind,
                content_key,
                snapshot_sha,
                content_digest,
                byte_length,
                content_bytes,
                artifact_schema_version,
                producer,
                producer_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """;

    private static final String SELECT_EXECUTION = """
            SELECT
                execution.schema_version,
                execution.id AS execution_id,
                execution.project_id,
                execution.repository_id,
                execution.pull_request_id,
                execution.base_sha,
                execution.head_sha,
                execution.merge_base_sha,
                execution.diff_artifact_id,
                execution.diff_digest,
                execution.diff_byte_length,
                execution.diff_artifact_kind,
                execution.diff_artifact_producer,
                execution.diff_artifact_producer_version,
                execution.artifact_schema_version AS manifest_artifact_schema_version,
                execution.policy_version,
                execution.creation_fence,
                execution.created_at,
                execution.artifact_manifest_digest AS manifest_digest,
                artifact.id AS artifact_id,
                artifact.execution_id AS artifact_execution_id,
                artifact.artifact_manifest_digest AS artifact_manifest_digest,
                artifact.kind AS artifact_kind,
                artifact.content_key AS artifact_content_key,
                artifact.snapshot_sha AS artifact_snapshot_sha,
                artifact.content_digest AS artifact_content_digest,
                artifact.byte_length AS artifact_byte_length,
                artifact.content_bytes AS artifact_content_bytes,
                artifact.artifact_schema_version AS entry_artifact_schema_version,
                artifact.producer AS artifact_producer,
                artifact.producer_version AS artifact_producer_version
            FROM review_execution execution
            LEFT JOIN review_artifact artifact
                ON artifact.execution_id = execution.id
                AND artifact.kind <> 'review-output'
                AND artifact.content_bytes IS NOT NULL
            WHERE execution.id = ?
            ORDER BY artifact.id
            """;

    private final DataSource dataSource;

    public PostgresExecutionManifestPersistenceAdapter(DataSource dataSource) {
        this.dataSource = Objects.requireNonNull(dataSource, "dataSource");
    }

    @Override
    public PersistedExecution createOrLoad(
            ImmutableExecutionManifest manifest,
            List<ExecutionArtifactPayload> inputArtifacts) {
        Objects.requireNonNull(manifest, "manifest");
        List<ExecutionArtifactPayload> artifacts = List.copyOf(
                Objects.requireNonNull(inputArtifacts, "inputArtifacts"));
        requireExactInputArtifacts(manifest, artifacts);

        try (Connection connection = dataSource.getConnection()) {
            connection.setTransactionIsolation(Connection.TRANSACTION_READ_COMMITTED);
            connection.setAutoCommit(false);
            try {
                int inserted = insertExecution(connection, manifest);
                if (inserted == 1) {
                    for (ExecutionArtifactPayload artifact : artifacts) {
                        insertArtifact(connection, manifest, artifact);
                    }
                }

                PersistedExecution persisted = selectByExecutionId(
                        connection,
                        manifest.executionId())
                        .orElseThrow(() -> new IllegalStateException(
                                "execution create-or-load returned no durable row"));
                connection.commit();
                return persisted;
            } catch (SQLException error) {
                rollback(connection, error);
                throw persistenceFailure("atomic execution create-or-load failed", error);
            } catch (RuntimeException error) {
                rollback(connection, error);
                throw error;
            }
        } catch (SQLException error) {
            throw persistenceFailure("unable to access execution manifest storage", error);
        }
    }

    @Override
    public Optional<PersistedExecution> findByExecutionId(String executionId) {
        if (executionId == null || executionId.isBlank()) {
            throw new IllegalArgumentException("executionId must not be blank");
        }
        try (Connection connection = dataSource.getConnection()) {
            return selectByExecutionId(connection, executionId);
        } catch (SQLException error) {
            throw persistenceFailure("unable to read execution manifest", error);
        }
    }

    private static int insertExecution(
            Connection connection,
            ImmutableExecutionManifest manifest) throws SQLException {
        try (PreparedStatement statement = connection.prepareStatement(INSERT_EXECUTION)) {
            int parameter = 1;
            statement.setString(parameter++, manifest.executionId());
            statement.setInt(parameter++, manifest.schemaVersion());
            statement.setLong(parameter++, manifest.projectId());
            statement.setString(parameter++, manifest.repositoryId());
            statement.setLong(parameter++, manifest.pullRequestId());
            statement.setString(parameter++, manifest.baseSha());
            statement.setString(parameter++, manifest.headSha());
            statement.setString(parameter++, manifest.mergeBaseSha());
            statement.setString(parameter++, manifest.diffArtifactId());
            statement.setString(parameter++, manifest.diffDigest());
            statement.setLong(parameter++, manifest.diffByteLength());
            statement.setString(parameter++, manifest.diffArtifactKind());
            statement.setString(parameter++, manifest.diffArtifactProducer());
            statement.setString(parameter++, manifest.diffArtifactProducerVersion());
            statement.setString(parameter++, manifest.artifactSchemaVersion());
            statement.setString(parameter++, manifest.policyVersion());
            statement.setString(parameter++, manifest.creationFence());
            statement.setTimestamp(parameter++, Timestamp.from(manifest.createdAt()));
            statement.setString(parameter, manifest.artifactManifestDigest());
            return statement.executeUpdate();
        }
    }

    private static void insertArtifact(
            Connection connection,
            ImmutableExecutionManifest manifest,
            ExecutionArtifactPayload payload) throws SQLException {
        ArtifactManifestEntry entry = payload.entry();
        try (PreparedStatement statement = connection.prepareStatement(INSERT_ARTIFACT)) {
            int parameter = 1;
            statement.setString(parameter++, entry.artifactId());
            statement.setString(parameter++, entry.executionId());
            statement.setString(parameter++, manifest.artifactManifestDigest());
            statement.setString(parameter++, databaseKind(entry.kind()));
            statement.setString(parameter++, entry.contentKey());
            statement.setString(parameter++, entry.snapshotSha());
            statement.setString(parameter++, entry.contentDigest());
            statement.setLong(parameter++, entry.byteLength());
            statement.setBytes(parameter++, payload.content());
            statement.setString(parameter++, entry.artifactSchemaVersion());
            statement.setString(parameter++, entry.producer());
            statement.setString(parameter, entry.producerVersion());
            if (statement.executeUpdate() != 1) {
                throw new SQLException("input artifact was not inserted atomically");
            }
        }
    }

    private static Optional<PersistedExecution> selectByExecutionId(
            Connection connection,
            String executionId) throws SQLException {
        try (PreparedStatement statement = connection.prepareStatement(SELECT_EXECUTION)) {
            statement.setString(1, executionId);
            try (ResultSet result = statement.executeQuery()) {
                if (!result.next()) {
                    return Optional.empty();
                }
                return Optional.of(reconstruct(result));
            }
        }
    }

    private static PersistedExecution reconstruct(ResultSet result) throws SQLException {
        try {
            List<ExecutionArtifactPayload> artifacts = new ArrayList<>();
            int schemaVersion = result.getInt("schema_version");
            String persistedExecutionId = result.getString("execution_id");
            long projectId = result.getLong("project_id");
            String repositoryId = result.getString("repository_id");
            long pullRequestId = result.getLong("pull_request_id");
            String baseSha = result.getString("base_sha");
            String headSha = result.getString("head_sha");
            String mergeBaseSha = result.getString("merge_base_sha");
            String diffArtifactId = result.getString("diff_artifact_id");
            String diffDigest = result.getString("diff_digest");
            long diffByteLength = result.getLong("diff_byte_length");
            String diffArtifactKind = result.getString("diff_artifact_kind");
            String diffArtifactProducer = result.getString("diff_artifact_producer");
            String diffArtifactProducerVersion = result.getString(
                    "diff_artifact_producer_version");
            String artifactSchemaVersion = result.getString(
                    "manifest_artifact_schema_version");
            String policyVersion = result.getString("policy_version");
            String creationFence = result.getString("creation_fence");
            java.time.Instant createdAt = result.getTimestamp("created_at").toInstant();
            String manifestDigest = result.getString("manifest_digest");
            do {
                String artifactId = result.getString("artifact_id");
                if (artifactId == null) {
                    continue;
                }
                String artifactManifestDigest = result.getString("artifact_manifest_digest");
                if (!manifestDigest.equals(artifactManifestDigest)) {
                    throw new IllegalStateException(
                            "persisted artifact belongs to another artifact manifest");
                }
                ArtifactManifestEntry artifact = new ArtifactManifestEntry(
                        result.getString("artifact_execution_id"),
                        artifactId,
                        result.getString("artifact_content_key"),
                        result.getString("artifact_snapshot_sha"),
                        result.getString("artifact_content_digest"),
                        result.getLong("artifact_byte_length"),
                        domainKind(result.getString("artifact_kind")),
                        result.getString("entry_artifact_schema_version"),
                        result.getString("artifact_producer"),
                        result.getString("artifact_producer_version"));
                artifacts.add(new ExecutionArtifactPayload(
                        artifact,
                        result.getBytes("artifact_content_bytes")));
            } while (result.next());
            artifacts.sort(Comparator.comparing(
                    payload -> payload.entry().artifactId()));

            ImmutableExecutionManifest manifest = new ImmutableExecutionManifest(
                    schemaVersion,
                    persistedExecutionId,
                    projectId,
                    repositoryId,
                    pullRequestId,
                    baseSha,
                    headSha,
                    mergeBaseSha,
                    diffArtifactId,
                    diffDigest,
                    diffByteLength,
                    diffArtifactKind,
                    diffArtifactProducer,
                    diffArtifactProducerVersion,
                    artifactSchemaVersion,
                    policyVersion,
                    creationFence,
                    createdAt,
                    artifacts.stream().map(ExecutionArtifactPayload::entry).toList(),
                    manifestDigest);
            return new PersistedExecution(manifest, artifacts);
        } catch (IllegalArgumentException error) {
            throw new IllegalStateException(
                    "persisted execution manifest failed domain validation",
                    error);
        }
    }

    private static void requireExactInputArtifacts(
            ImmutableExecutionManifest manifest,
            List<ExecutionArtifactPayload> payloads) {
        List<ArtifactManifestEntry> entries = payloads.stream()
                .map(ExecutionArtifactPayload::entry)
                .toList();
        if (!manifest.inputArtifacts().equals(entries)) {
            throw new IllegalArgumentException(
                    "input artifacts do not match immutable manifest coordinates");
        }
    }

    private static String databaseKind(ArtifactManifestEntry.Kind kind) {
        return switch (kind) {
            case RAW_DIFF -> "raw-diff";
            case SOURCE_FILE -> "source-file";
            case PR_ENRICHMENT -> "pr-enrichment";
            case EXECUTION_CONFIG -> "execution-config";
            case REVIEW_OUTPUT -> "review-output";
        };
    }

    private static ArtifactManifestEntry.Kind domainKind(String kind) {
        return switch (kind) {
            case "raw-diff" -> ArtifactManifestEntry.Kind.RAW_DIFF;
            case "source-file" -> ArtifactManifestEntry.Kind.SOURCE_FILE;
            case "pr-enrichment" -> ArtifactManifestEntry.Kind.PR_ENRICHMENT;
            case "execution-config" -> ArtifactManifestEntry.Kind.EXECUTION_CONFIG;
            case "review-output" -> ArtifactManifestEntry.Kind.REVIEW_OUTPUT;
            default -> throw new IllegalStateException(
                    "persisted artifact kind is unsupported: " + kind);
        };
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

}
