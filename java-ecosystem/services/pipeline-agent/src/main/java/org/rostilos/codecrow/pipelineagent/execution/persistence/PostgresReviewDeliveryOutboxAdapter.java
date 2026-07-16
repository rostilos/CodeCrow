package org.rostilos.codecrow.pipelineagent.execution.persistence;

import org.rostilos.codecrow.analysisengine.delivery.ReviewDeliveryClaim;
import org.rostilos.codecrow.analysisengine.delivery.ReviewDeliveryHead;
import org.rostilos.codecrow.analysisengine.delivery.ReviewDeliveryIntent;
import org.rostilos.codecrow.analysisengine.delivery.ReviewDeliveryOutboxPort;
import org.rostilos.codecrow.analysisengine.delivery.ReviewDeliveryOutcome;
import org.rostilos.codecrow.analysisengine.delivery.ReviewDeliveryState;
import org.rostilos.codecrow.analysisengine.delivery.ReviewProviderEffectIdentity;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Repository;

import javax.sql.DataSource;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Objects;
import java.util.Optional;
import java.util.UUID;

/** PostgreSQL outbox with transactional stale-head admission and exact receipts. */
@Repository
public class PostgresReviewDeliveryOutboxAdapter
        implements ReviewDeliveryOutboxPort {

    private static final String INTENT_COLUMNS = """
            intent_id, execution_id, artifact_manifest_digest,
            report_artifact_id, report_digest, analysis_truth_digest,
            provider, project_id, pull_request_id, head_sha,
            head_generation, publication_kind, idempotency_key
            """;
    private static final String OUTCOME_COLUMNS = """
            state, intent_id, idempotency_key, attempt_count,
            last_error_code, provider_receipt_id
            """;

    private final DataSource dataSource;
    private final Duration leaseDuration;
    private final Duration retryDelay;

    public PostgresReviewDeliveryOutboxAdapter(
            DataSource dataSource,
            @Value("${codecrow.review.delivery.lease-duration:PT1M}")
            Duration leaseDuration,
            @Value("${codecrow.review.delivery.retry-delay:PT30S}")
            Duration retryDelay) {
        this.dataSource = Objects.requireNonNull(dataSource, "dataSource");
        this.leaseDuration = requireDuration(leaseDuration, "leaseDuration", false);
        this.retryDelay = requireDuration(retryDelay, "retryDelay", true);
    }

    @Override
    public ReviewDeliveryHead registerCurrentHead(ReviewDeliveryHead proposed) {
        Objects.requireNonNull(proposed, "proposed");
        try (Connection connection = dataSource.getConnection()) {
            connection.setAutoCommit(false);
            try {
                HeadBinding binding = requireHeadBinding(connection, proposed);
                try (PreparedStatement statement = connection.prepareStatement("""
                        INSERT INTO review_delivery_current_head (
                            provider, tenant_id, project_id, repository_id,
                            pull_request_id, head_generation, execution_id,
                            artifact_manifest_digest, head_sha
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT (provider, project_id, pull_request_id)
                        DO UPDATE SET
                            tenant_id = EXCLUDED.tenant_id,
                            repository_id = EXCLUDED.repository_id,
                            head_generation = EXCLUDED.head_generation,
                            execution_id = EXCLUDED.execution_id,
                            artifact_manifest_digest =
                                EXCLUDED.artifact_manifest_digest,
                            head_sha = EXCLUDED.head_sha
                        WHERE review_delivery_current_head.head_generation
                                  < EXCLUDED.head_generation
                           OR (
                               review_delivery_current_head.head_generation
                                   = EXCLUDED.head_generation
                               AND review_delivery_current_head.execution_id
                                   = EXCLUDED.execution_id
                               AND review_delivery_current_head.artifact_manifest_digest
                                   = EXCLUDED.artifact_manifest_digest
                               AND review_delivery_current_head.head_sha
                                   = EXCLUDED.head_sha
                           )
                        """)) {
                    statement.setString(1, proposed.provider());
                    statement.setLong(2, proposed.tenantId());
                    statement.setLong(3, proposed.projectId());
                    statement.setString(4, proposed.repositoryId());
                    statement.setLong(5, proposed.pullRequestId());
                    statement.setLong(6, proposed.generation());
                    statement.setString(7, proposed.executionId());
                    statement.setString(8, binding.artifactManifestDigest());
                    statement.setString(9, proposed.headRevision());
                    statement.executeUpdate();
                }

                CurrentHeadRow stored = lockCurrentHead(
                                connection,
                                proposed.provider(),
                                proposed.projectId(),
                                proposed.pullRequestId())
                        .orElseThrow(() -> new IllegalStateException(
                                "delivery current head was not persisted"));
                if (stored.head().generation() == proposed.generation()
                        && !stored.head().equals(proposed)) {
                    throw new IllegalStateException(
                            "delivery head generation conflicts with durable identity");
                }
                if (stored.head().generation() < proposed.generation()) {
                    throw new IllegalStateException(
                            "delivery current-head generation did not advance");
                }
                connection.commit();
                return stored.head();
            } catch (SQLException | RuntimeException error) {
                rollback(connection, error);
                throw error;
            }
        } catch (SQLException error) {
            throw failure("unable to register delivery current head", error);
        }
    }

    /** Returns empty for a transactionally observed stale_head without inserting. */
    @Override
    public Optional<ReviewDeliveryIntent> createOrLoadIfCurrent(
            ReviewDeliveryIntent proposed) {
        Objects.requireNonNull(proposed, "proposed");
        try (Connection connection = dataSource.getConnection()) {
            connection.setAutoCommit(false);
            try {
                Optional<CurrentHeadRow> locked = lockCurrentHead(
                        connection,
                        proposed.provider(),
                        proposed.projectId(),
                        proposed.pullRequestId());
                if (locked.isEmpty()
                        || !currentHeadMatches(locked.orElseThrow(), proposed)) {
                    connection.rollback();
                    return Optional.empty();
                }
                CurrentHeadRow currentHead = locked.orElseThrow();
                requireProviderEffectIdentity(proposed, currentHead.head());
                TargetIds target = requireTarget(connection, proposed);
                try (PreparedStatement statement = connection.prepareStatement("""
                        INSERT INTO review_delivery_outbox (
                            intent_id, execution_id, artifact_manifest_digest,
                            code_analysis_id, report_artifact_id, report_digest,
                            analysis_truth_digest, provider, tenant_id,
                            project_id, repository_id, pull_request_id,
                            platform_pull_request_id, head_sha, head_generation,
                            publication_kind, idempotency_key, state,
                            attempt_count, next_attempt_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                  ?, ?,
                                  'PENDING', 0, CURRENT_TIMESTAMP)
                        ON CONFLICT DO NOTHING
                        """)) {
                    int index = 1;
                    statement.setString(index++, proposed.intentId());
                    statement.setString(index++, proposed.executionId());
                    statement.setString(index++, proposed.artifactManifestDigest());
                    statement.setLong(index++, target.analysisId());
                    statement.setString(index++, proposed.reportArtifactId());
                    statement.setString(index++, proposed.reportDigest());
                    statement.setString(index++, proposed.analysisTruthDigest());
                    statement.setString(index++, proposed.provider());
                    statement.setLong(index++, currentHead.head().tenantId());
                    statement.setLong(index++, proposed.projectId());
                    statement.setString(
                            index++, currentHead.head().repositoryId());
                    statement.setLong(index++, proposed.pullRequestId());
                    statement.setLong(index++, target.platformPullRequestId());
                    statement.setString(index++, proposed.snapshotRevision());
                    statement.setLong(index++, proposed.headGeneration());
                    statement.setString(index++, proposed.publicationKind());
                    statement.setString(index, proposed.idempotencyKey());
                    statement.executeUpdate();
                }
                ReviewDeliveryIntent stored = findIntent(
                                connection, proposed.intentId())
                        .orElseThrow(() -> new IllegalStateException(
                                "delivery identity conflicts with an existing intent"));
                if (!proposed.equals(stored)) {
                    throw new IllegalStateException(
                            "delivery intent conflicts with durable analysis truth");
                }
                requireStoredDeliveryScope(
                        connection, proposed.intentId(), currentHead.head());
                connection.commit();
                return Optional.of(stored);
            } catch (SQLException | RuntimeException error) {
                rollback(connection, error);
                throw error;
            }
        } catch (SQLException error) {
            throw failure("unable to create or load delivery intent", error);
        }
    }

    @Override
    public Optional<ReviewDeliveryIntent> findIntent(String intentId) {
        requireText(intentId, "intentId");
        try (Connection connection = dataSource.getConnection()) {
            return findIntent(connection, intentId);
        } catch (SQLException error) {
            throw failure("unable to read delivery intent", error);
        }
    }

    @Override
    public Optional<ReviewDeliveryClaim> tryClaim(String intentId, Instant now) {
        requireText(intentId, "intentId");
        Objects.requireNonNull(now, "now");
        String leaseToken = "lease:" + UUID.randomUUID();
        String leaseOwner = "pipeline-agent";
        String sql = """
                UPDATE review_delivery_outbox
                SET state = 'IN_FLIGHT',
                    attempt_count = attempt_count + 1,
                    lease_owner = ?,
                    lease_token = ?,
                    lease_expires_at = ?,
                    last_error_code = NULL,
                    provider_receipt_id = NULL,
                    delivered_at = NULL
                WHERE intent_id = ?
                  AND (
                    (state IN ('PENDING', 'RETRYABLE_FAILED')
                        AND next_attempt_at <= ?)
                    OR (state = 'IN_FLIGHT' AND lease_expires_at <= ?)
                  )
                RETURNING %s, attempt_count, lease_token
                """.formatted(INTENT_COLUMNS);
        try (Connection connection = dataSource.getConnection();
                PreparedStatement statement = connection.prepareStatement(sql)) {
            statement.setString(1, leaseOwner);
            statement.setString(2, leaseToken);
            statement.setTimestamp(3, timestamp(now.plus(leaseDuration)));
            statement.setString(4, intentId);
            statement.setTimestamp(5, timestamp(now));
            statement.setTimestamp(6, timestamp(now));
            try (ResultSet result = statement.executeQuery()) {
                if (!result.next()) {
                    return Optional.empty();
                }
                return Optional.of(new ReviewDeliveryClaim(
                        mapIntent(result),
                        result.getInt("attempt_count"),
                        result.getString("lease_token")));
            }
        } catch (SQLException error) {
            throw failure("unable to claim delivery intent", error);
        }
    }

    @Override
    public ReviewDeliveryOutcome markEffectStarted(
            ReviewDeliveryClaim claim,
            Instant now) {
        Objects.requireNonNull(claim, "claim");
        Objects.requireNonNull(now, "now");
        ReviewDeliveryOutcome started = new ReviewDeliveryOutcome(
                ReviewDeliveryState.AMBIGUOUS,
                claim.intent().intentId(),
                claim.intent().idempotencyKey(),
                claim.attemptNumber(),
                "provider_ack_unknown",
                null);
        String sql = """
                UPDATE review_delivery_outbox
                SET state = 'AMBIGUOUS',
                    last_error_code = 'provider_ack_unknown'
                WHERE intent_id = ? AND state = 'IN_FLIGHT'
                  AND lease_token = ? AND attempt_count = ?
                RETURNING %s
                """.formatted(OUTCOME_COLUMNS);
        try (Connection connection = dataSource.getConnection();
                PreparedStatement statement = connection.prepareStatement(sql)) {
            statement.setString(1, claim.intent().intentId());
            statement.setString(2, claim.leaseToken());
            statement.setInt(3, claim.attemptNumber());
            try (ResultSet result = statement.executeQuery()) {
                if (result.next()) {
                    return requireExact(started, mapOutcome(result),
                            "stored effect-start receipt conflicts with claim");
                }
            }
            return findClaimOutcome(claim, ReviewDeliveryState.AMBIGUOUS)
                    .filter(started::equals)
                    .orElseThrow(() -> new IllegalStateException(
                            "delivery effect start could not be persisted"));
        } catch (SQLException error) {
            throw failure("unable to mark delivery effect started", error);
        }
    }

    @Override
    public ReviewDeliveryOutcome recordOutcome(
            ReviewDeliveryClaim claim,
            ReviewDeliveryOutcome outcome,
            Instant now) {
        Objects.requireNonNull(claim, "claim");
        Objects.requireNonNull(outcome, "outcome");
        Objects.requireNonNull(now, "now");
        requireClaimOutcome(claim, outcome);
        if (outcome.state() == ReviewDeliveryState.PENDING
                || outcome.state() == ReviewDeliveryState.IN_FLIGHT) {
            throw new IllegalArgumentException(
                    "delivery outcome must be terminal or retryable");
        }
        Instant nextAttempt = outcome.state() == ReviewDeliveryState.RETRYABLE_FAILED
                ? now.plus(retryDelay) : now;
        Timestamp deliveredAt = outcome.state() == ReviewDeliveryState.DELIVERED
                ? timestamp(now) : null;
        String sql = """
                UPDATE review_delivery_outbox
                SET state = ?, next_attempt_at = ?,
                    lease_owner = NULL, lease_token = NULL,
                    lease_expires_at = NULL,
                    last_error_code = ?, provider_receipt_id = ?,
                    delivered_at = ?
                WHERE intent_id = ?
                  AND lease_token = ? AND attempt_count = ?
                  AND (
                    state = 'AMBIGUOUS'
                    OR (state = 'IN_FLIGHT' AND ? = 'STALE')
                  )
                RETURNING %s
                """.formatted(OUTCOME_COLUMNS);
        try (Connection connection = dataSource.getConnection();
                PreparedStatement statement = connection.prepareStatement(sql)) {
            statement.setString(1, outcome.state().name());
            statement.setTimestamp(2, timestamp(nextAttempt));
            statement.setString(3, outcome.reasonCode());
            statement.setString(4, outcome.providerReceiptId());
            statement.setTimestamp(5, deliveredAt);
            statement.setString(6, claim.intent().intentId());
            statement.setString(7, claim.leaseToken());
            statement.setInt(8, claim.attemptNumber());
            statement.setString(9, outcome.state().name());
            try (ResultSet result = statement.executeQuery()) {
                if (result.next()) {
                    return requireExact(outcome, mapOutcome(result),
                            "stored delivery outcome conflicts with attempt");
                }
            }
            return findOutcome(claim.intent().intentId())
                    .filter(outcome::equals)
                    .orElseThrow(() -> new IllegalStateException(
                            "delivery lease was lost before outcome acknowledgement"));
        } catch (SQLException error) {
            throw failure("unable to record delivery outcome", error);
        }
    }

    @Override
    public Optional<ReviewDeliveryOutcome> findOutcome(String intentId) {
        requireText(intentId, "intentId");
        String sql = "SELECT " + OUTCOME_COLUMNS
                + " FROM review_delivery_outbox WHERE intent_id = ?";
        try (Connection connection = dataSource.getConnection();
                PreparedStatement statement = connection.prepareStatement(sql)) {
            statement.setString(1, intentId);
            try (ResultSet result = statement.executeQuery()) {
                return result.next()
                        ? Optional.of(mapOutcome(result)) : Optional.empty();
            }
        } catch (SQLException error) {
            throw failure("unable to read delivery outcome", error);
        }
    }

    @Override
    public List<String> findDueIntentIds(Instant now, int limit) {
        Objects.requireNonNull(now, "now");
        if (limit < 1 || limit > 1_000) {
            throw new IllegalArgumentException("delivery due limit must be 1..1000");
        }
        String sql = """
                SELECT intent_id FROM review_delivery_outbox
                WHERE (state IN ('PENDING', 'RETRYABLE_FAILED')
                           AND next_attempt_at <= ?)
                   OR (state = 'IN_FLIGHT' AND lease_expires_at <= ?)
                ORDER BY next_attempt_at, intent_id
                LIMIT ?
                """;
        try (Connection connection = dataSource.getConnection();
                PreparedStatement statement = connection.prepareStatement(sql)) {
            statement.setTimestamp(1, timestamp(now));
            statement.setTimestamp(2, timestamp(now));
            statement.setInt(3, limit);
            List<String> ids = new ArrayList<>();
            try (ResultSet result = statement.executeQuery()) {
                while (result.next()) {
                    ids.add(result.getString(1));
                }
            }
            return List.copyOf(ids);
        } catch (SQLException error) {
            throw failure("unable to find due delivery intents", error);
        }
    }

    private Optional<CurrentHeadRow> lockCurrentHead(
            Connection connection,
            String provider,
            long projectId,
            long pullRequestId) throws SQLException {
        String sql = """
                SELECT current_head.provider,
                       current_head.tenant_id,
                       current_head.project_id,
                       current_head.repository_id AS current_repository_id,
                       current_head.pull_request_id,
                       current_head.head_generation,
                       current_head.execution_id,
                       current_head.artifact_manifest_digest,
                       current_head.head_sha,
                       p.workspace_id,
                       e.repository_id AS execution_repository_id
                FROM review_delivery_current_head current_head
                JOIN review_execution e
                  ON e.id = current_head.execution_id
                 AND e.artifact_manifest_digest =
                     current_head.artifact_manifest_digest
                 AND e.project_id = current_head.project_id
                 AND e.pull_request_id = current_head.pull_request_id
                 AND e.head_sha = current_head.head_sha
                JOIN project p ON p.id = current_head.project_id
                WHERE current_head.provider = ?
                  AND current_head.project_id = ?
                  AND current_head.pull_request_id = ?
                FOR UPDATE OF current_head
                """;
        try (PreparedStatement statement = connection.prepareStatement(sql)) {
            statement.setString(1, provider);
            statement.setLong(2, projectId);
            statement.setLong(3, pullRequestId);
            try (ResultSet result = statement.executeQuery()) {
                if (!result.next()) {
                    return Optional.empty();
                }
                String storedProvider = result.getString("provider");
                ReviewDeliveryHead head = new ReviewDeliveryHead(
                        storedProvider,
                        result.getLong("tenant_id"),
                        result.getLong("project_id"),
                        result.getString("current_repository_id"),
                        result.getLong("pull_request_id"),
                        result.getString("execution_id"),
                        result.getString("head_sha"),
                        result.getLong("head_generation"));
                if (head.tenantId() != result.getLong("workspace_id")
                        || !head.repositoryId().equals(providerRepositoryId(
                                storedProvider,
                                result.getString("execution_repository_id")))) {
                    throw new IllegalStateException(
                            "delivery current head conflicts with tenant or repository truth");
                }
                return Optional.of(new CurrentHeadRow(
                        head,
                        result.getString("artifact_manifest_digest")));
            }
        }
    }

    private HeadBinding requireHeadBinding(
            Connection connection,
            ReviewDeliveryHead proposed) throws SQLException {
        String sql = """
                SELECT e.artifact_manifest_digest,
                       p.workspace_id,
                       e.repository_id
                FROM review_execution e
                JOIN project p ON p.id = e.project_id
                WHERE e.id = ?
                  AND e.project_id = ?
                  AND e.pull_request_id = ?
                  AND e.head_sha = ?
                FOR KEY SHARE OF e, p
                """;
        try (PreparedStatement statement = connection.prepareStatement(sql)) {
            statement.setString(1, proposed.executionId());
            statement.setLong(2, proposed.projectId());
            statement.setLong(3, proposed.pullRequestId());
            statement.setString(4, proposed.headRevision());
            try (ResultSet result = statement.executeQuery()) {
                if (!result.next()) {
                    throw new IllegalStateException(
                            "delivery head is not bound to a persisted execution");
                }
                long tenantId = result.getLong("workspace_id");
                String repositoryId = providerRepositoryId(
                        proposed.provider(), result.getString("repository_id"));
                if (tenantId != proposed.tenantId()
                        || !repositoryId.equals(proposed.repositoryId())) {
                    throw new IllegalStateException(
                            "delivery head conflicts with tenant or repository identity");
                }
                return new HeadBinding(
                        result.getString("artifact_manifest_digest"));
            }
        }
    }

    private TargetIds requireTarget(
            Connection connection, ReviewDeliveryIntent intent) throws SQLException {
        String sql = """
                SELECT ca.id AS analysis_id, pr.id AS platform_pr_id
                FROM code_analysis ca
                JOIN pull_request pr
                  ON pr.project_id = ca.project_id
                 AND pr.pr_number = ca.pr_number
                WHERE ca.execution_id = ?
                  AND ca.artifact_manifest_digest = ?
                  AND ca.project_id = ?
                  AND ca.pr_number = ?
                  AND ca.commit_hash = ?
                FOR KEY SHARE
                """;
        try (PreparedStatement statement = connection.prepareStatement(sql)) {
            statement.setString(1, intent.executionId());
            statement.setString(2, intent.artifactManifestDigest());
            statement.setLong(3, intent.projectId());
            statement.setLong(4, intent.pullRequestId());
            statement.setString(5, intent.snapshotRevision());
            try (ResultSet result = statement.executeQuery()) {
                if (!result.next()) {
                    throw new IllegalStateException(
                            "delivery target is not bound to persisted analysis truth");
                }
                return new TargetIds(
                        result.getLong("analysis_id"),
                        result.getLong("platform_pr_id"));
            }
        }
    }

    private Optional<ReviewDeliveryIntent> findIntent(
            Connection connection, String intentId) throws SQLException {
        String sql = "SELECT " + INTENT_COLUMNS
                + " FROM review_delivery_outbox WHERE intent_id = ?";
        try (PreparedStatement statement = connection.prepareStatement(sql)) {
            statement.setString(1, intentId);
            try (ResultSet result = statement.executeQuery()) {
                return result.next()
                        ? Optional.of(mapIntent(result)) : Optional.empty();
            }
        }
    }

    private void requireStoredDeliveryScope(
            Connection connection,
            String intentId,
            ReviewDeliveryHead currentHead) throws SQLException {
        String sql = """
                SELECT tenant_id, repository_id
                FROM review_delivery_outbox
                WHERE intent_id = ?
                FOR KEY SHARE
                """;
        try (PreparedStatement statement = connection.prepareStatement(sql)) {
            statement.setString(1, intentId);
            try (ResultSet result = statement.executeQuery()) {
                if (!result.next()
                        || result.getLong("tenant_id") != currentHead.tenantId()
                        || !currentHead.repositoryId().equals(
                                result.getString("repository_id"))) {
                    throw new IllegalStateException(
                            "delivery intent conflicts with durable provider scope");
                }
            }
        }
    }

    private Optional<ReviewDeliveryOutcome> findClaimOutcome(
            ReviewDeliveryClaim claim,
            ReviewDeliveryState state) {
        String sql = "SELECT " + OUTCOME_COLUMNS + """
                FROM review_delivery_outbox
                WHERE intent_id = ? AND state = ?
                  AND lease_token = ? AND attempt_count = ?
                """;
        try (Connection connection = dataSource.getConnection();
                PreparedStatement statement = connection.prepareStatement(sql)) {
            statement.setString(1, claim.intent().intentId());
            statement.setString(2, state.name());
            statement.setString(3, claim.leaseToken());
            statement.setInt(4, claim.attemptNumber());
            try (ResultSet result = statement.executeQuery()) {
                return result.next()
                        ? Optional.of(mapOutcome(result)) : Optional.empty();
            }
        } catch (SQLException error) {
            throw failure("unable to read delivery claim outcome", error);
        }
    }

    private static boolean currentHeadMatches(
            CurrentHeadRow currentHead,
            ReviewDeliveryIntent proposed) {
        return currentHead.head().executionId().equals(proposed.executionId())
                && currentHead.head().headRevision().equals(
                        proposed.snapshotRevision())
                && currentHead.head().generation() == proposed.headGeneration()
                && currentHead.artifactManifestDigest().equals(
                        proposed.artifactManifestDigest());
    }

    private static void requireProviderEffectIdentity(
            ReviewDeliveryIntent proposed,
            ReviewDeliveryHead currentHead) {
        String expected = ReviewProviderEffectIdentity.derive(
                currentHead.tenantId(),
                proposed.provider(),
                currentHead.repositoryId(),
                proposed.pullRequestId(),
                proposed.snapshotRevision(),
                proposed.reportDigest(),
                proposed.publicationKind());
        if (!expected.equals(proposed.idempotencyKey())) {
            throw new IllegalStateException(
                    "delivery provider effect identity conflicts with durable coordinates");
        }
    }

    private static void requireClaimOutcome(
            ReviewDeliveryClaim claim,
            ReviewDeliveryOutcome outcome) {
        if (!claim.intent().intentId().equals(outcome.intentId())
                || !claim.intent().idempotencyKey().equals(
                        outcome.idempotencyKey())
                || claim.attemptNumber() != outcome.attemptCount()) {
            throw new IllegalArgumentException(
                    "delivery outcome conflicts with claim");
        }
    }

    private static <T> T requireExact(T proposed, T stored, String message) {
        if (!proposed.equals(stored)) {
            throw new IllegalStateException(message);
        }
        return stored;
    }

    private static ReviewDeliveryIntent mapIntent(ResultSet result)
            throws SQLException {
        return new ReviewDeliveryIntent(
                result.getString("intent_id"),
                result.getString("execution_id"),
                result.getString("artifact_manifest_digest"),
                result.getString("head_sha"),
                result.getLong("head_generation"),
                result.getString("report_artifact_id"),
                result.getString("report_digest"),
                result.getString("analysis_truth_digest"),
                result.getString("provider"),
                result.getLong("project_id"),
                result.getLong("pull_request_id"),
                result.getString("publication_kind"),
                result.getString("idempotency_key"));
    }

    private static ReviewDeliveryOutcome mapOutcome(ResultSet result)
            throws SQLException {
        return new ReviewDeliveryOutcome(
                ReviewDeliveryState.valueOf(result.getString("state")),
                result.getString("intent_id"),
                result.getString("idempotency_key"),
                result.getInt("attempt_count"),
                result.getString("last_error_code"),
                result.getString("provider_receipt_id"));
    }

    private static String providerRepositoryId(
            String provider, String manifestRepositoryId) {
        String prefix = provider + ':';
        if (manifestRepositoryId == null
                || !manifestRepositoryId.startsWith(prefix)
                || manifestRepositoryId.length() == prefix.length()) {
            throw new IllegalStateException(
                    "delivery repository conflicts with provider identity");
        }
        return manifestRepositoryId;
    }

    private static Duration requireDuration(
            Duration value, String field, boolean zeroAllowed) {
        if (value == null || value.isNegative()
                || (!zeroAllowed && value.isZero())) {
            throw new IllegalArgumentException(field + " is invalid");
        }
        return value;
    }

    private static void requireText(String value, String field) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(field + " must not be blank");
        }
    }

    private static Timestamp timestamp(Instant value) {
        return value == null ? null : Timestamp.from(value);
    }

    private static void rollback(Connection connection, Throwable cause) {
        try {
            connection.rollback();
        } catch (SQLException rollbackFailure) {
            cause.addSuppressed(rollbackFailure);
        }
    }

    private static IllegalStateException failure(String message, SQLException error) {
        return new IllegalStateException(message, error);
    }

    private record HeadBinding(String artifactManifestDigest) {
    }

    private record CurrentHeadRow(
            ReviewDeliveryHead head,
            String artifactManifestDigest) {
    }

    private record TargetIds(long analysisId, long platformPullRequestId) {
    }
}
