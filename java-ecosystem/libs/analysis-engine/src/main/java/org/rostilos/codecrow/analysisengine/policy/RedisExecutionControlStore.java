package org.rostilos.codecrow.analysisengine.policy;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.List;
import java.util.Objects;
import java.util.Optional;
import java.util.OptionalLong;

/**
 * Redis-backed scaffold with distinct plan, primary-artifact, shadow-artifact,
 * and publication-claim namespaces. Values intentionally have no rollout-time
 * deletion path; retention is introduced by P1-11.
 */
@Service
public class RedisExecutionControlStore implements ExecutionControlStore {
    static final String PREFIX = "codecrow:llm-handoff:policy:v1:";
    static final String CANDIDATE_KILL_SWITCH_KEY =
            PREFIX + "canary-rollback-v1:candidate-kill-switch";
    private static final DefaultRedisScript<Long> CLAIM_LATEST_HEAD_GENERATION =
            new DefaultRedisScript<>("""
                    local existing = redis.call('GET', KEYS[2])
                    if existing then
                        return tonumber(existing)
                    end
                    local generation = redis.call('INCR', KEYS[1])
                    redis.call('SET', KEYS[2], generation)
                    redis.call('HSET', KEYS[3], generation, ARGV[1])
                    return generation
                    """, Long.class);
    private static final DefaultRedisScript<Long> REGISTER_LATEST_HEAD =
            new DefaultRedisScript<>("""
                    if redis.call('HEXISTS', KEYS[4], ARGV[1]) ~= 1 then
                        return -3
                    end
                    local current_generation = redis.call('GET', KEYS[1])
                    if current_generation then
                        local current_number = tonumber(current_generation)
                        local candidate_number = tonumber(ARGV[1])
                        if candidate_number < current_number then
                            return -1
                        end
                        if candidate_number == current_number then
                            local current_execution = redis.call('GET', KEYS[2])
                            local current_head = redis.call('GET', KEYS[3])
                            if current_execution == ARGV[2] and current_head == ARGV[3] then
                                return 0
                            end
                            return -2
                        end
                    end
                    redis.call('SET', KEYS[1], ARGV[1])
                    redis.call('SET', KEYS[2], ARGV[2])
                    redis.call('SET', KEYS[3], ARGV[3])
                    return 1
                    """, Long.class);
    private static final DefaultRedisScript<Long> CLAIM_LATEST_PUBLICATION =
            new DefaultRedisScript<>("""
                    if redis.call('GET', KEYS[1]) ~= ARGV[1]
                            or redis.call('GET', KEYS[2]) ~= ARGV[2] then
                        return -1
                    end
                    if redis.call('SETNX', KEYS[3], 'reserved') == 1 then
                        return 1
                    end
                    return 0
                    """, Long.class);
    private static final DefaultRedisScript<Long> CREATE_ARTIFACT_IF_ABSENT =
            new DefaultRedisScript<>("""
                    local claimed = redis.call('GET', KEYS[2])
                    if claimed then
                        return 0
                    end
                    local existing = redis.call('LRANGE', KEYS[1], 0, -1)
                    for _, value in ipairs(existing) do
                        local decoded = cjson.decode(value)
                        if decoded['artifactId'] == ARGV[2] then
                            redis.call('SET', KEYS[2], value)
                            return 0
                        end
                    end
                    redis.call('SET', KEYS[2], ARGV[1])
                    redis.call('RPUSH', KEYS[1], ARGV[1])
                    return 1
                    """, Long.class);

    private final StringRedisTemplate redis;
    private final ObjectMapper objectMapper;

    public RedisExecutionControlStore(
            @Qualifier("queueRedisTemplate") StringRedisTemplate redis,
            ObjectMapper objectMapper) {
        this.redis = Objects.requireNonNull(redis, "redis");
        this.objectMapper = Objects.requireNonNull(objectMapper, "objectMapper");
    }

    @Override
    public Optional<FrozenExecutionPlan> findPlan(String executionId) {
        String json = redis.opsForValue().get(planKey(executionId));
        if (json == null) {
            return Optional.empty();
        }
        return Optional.of(read(json, FrozenExecutionPlan.class));
    }

    @Override
    public FrozenExecutionPlan createPlanIfAbsent(FrozenExecutionPlan plan) {
        String key = planKey(plan.executionId());
        String json = write(plan);
        if (Boolean.TRUE.equals(redis.opsForValue().setIfAbsent(key, json))) {
            return plan;
        }
        String existing = redis.opsForValue().get(key);
        if (existing == null) {
            throw new IllegalStateException("execution policy plan claim exists without a value");
        }
        return read(existing, FrozenExecutionPlan.class);
    }

    @Override
    public void persistArtifact(ExecutionArtifact artifact) {
        redis.opsForList().rightPush(
                artifactKey(artifact.executionId(), artifact.namespace()),
                write(artifact));
    }

    @Override
    public ExecutionArtifact createArtifactIfAbsent(
            ExecutionArtifact artifact) {
        Objects.requireNonNull(artifact, "artifact");
        String json = write(artifact);
        String valueKey = artifactValueKey(
                artifact.executionId(), artifact.namespace(), artifact.artifactId());
        Long result = redis.execute(
                CREATE_ARTIFACT_IF_ABSENT,
                List.of(
                        artifactKey(artifact.executionId(), artifact.namespace()),
                        valueKey),
                json,
                artifact.artifactId());
        if (!Long.valueOf(0L).equals(result)
                && !Long.valueOf(1L).equals(result)) {
            throw new IllegalStateException(
                    "immutable artifact claim did not complete");
        }
        String persisted = redis.opsForValue().get(valueKey);
        if (persisted == null) {
            throw new IllegalStateException(
                    "immutable artifact claim exists without a value");
        }
        return read(persisted, ExecutionArtifact.class);
    }

    @Override
    public List<ExecutionArtifact> findArtifacts(
            String executionId,
            ArtifactNamespace namespace) {
        List<ExecutionArtifact> artifacts = new ArrayList<>();
        List<String> persisted = redis.opsForList().range(
                artifactKey(executionId, namespace), 0, -1);
        for (String json : persisted == null ? List.<String>of() : persisted) {
            artifacts.add(read(json, ExecutionArtifact.class));
        }
        return List.copyOf(artifacts);
    }

    @Override
    public boolean tryClaimPublication(String publicationClaimId) {
        return Boolean.TRUE.equals(redis.opsForValue().setIfAbsent(
                PREFIX + "publication-claim:" + publicationClaimId,
                "reserved"));
    }

    @Override
    public long claimLatestHeadGeneration(
            String publicationScopeId,
            String admissionId) {
        Objects.requireNonNull(publicationScopeId, "publicationScopeId");
        Objects.requireNonNull(admissionId, "admissionId");
        Long result = redis.execute(
                CLAIM_LATEST_HEAD_GENERATION,
                List.of(
                        generationCounterKey(publicationScopeId),
                        admissionGenerationKey(publicationScopeId, admissionId),
                        generationClaimKey(publicationScopeId)),
                sha256(admissionId));
        if (result == null || result <= 0L) {
            throw new IllegalStateException(
                    "latest-head generation claim did not complete");
        }
        return result;
    }

    /**
     * The installed value used by cross-process cancellation readers is stored
     * as a positive base-10 integer at
     * {@code codecrow:llm-handoff:policy:v1:{pr-<scope-id>}:latest-generation}.
     */
    @Override
    public OptionalLong findLatestHeadGeneration(String publicationScopeId) {
        Objects.requireNonNull(publicationScopeId, "publicationScopeId");
        String persisted = redis.opsForValue().get(
                latestGenerationKey(publicationScopeId));
        if (persisted == null) {
            return OptionalLong.empty();
        }
        try {
            long generation = Long.parseLong(persisted);
            if (generation <= 0L) {
                throw new NumberFormatException("generation must be positive");
            }
            return OptionalLong.of(generation);
        } catch (NumberFormatException error) {
            throw new IllegalStateException(
                    "persisted latest-head generation is invalid", error);
        }
    }

    @Override
    public LatestHeadRegistration registerLatestHead(
            String publicationScopeId,
            String executionId,
            String headRevision,
            long generation) {
        if (generation <= 0L) {
            throw new IllegalArgumentException(
                    "latest-head generation must be positive");
        }
        Long result = redis.execute(
                REGISTER_LATEST_HEAD,
                List.of(
                        latestGenerationKey(publicationScopeId),
                        latestExecutionKey(publicationScopeId),
                        latestRevisionKey(publicationScopeId),
                        generationClaimKey(publicationScopeId)),
                Long.toString(generation),
                executionId,
                headRevision);
        if (Long.valueOf(1L).equals(result)) {
            return LatestHeadRegistration.ACCEPTED;
        }
        if (Long.valueOf(0L).equals(result)) {
            return LatestHeadRegistration.DUPLICATE;
        }
        if (Long.valueOf(-1L).equals(result)) {
            return LatestHeadRegistration.SUPERSEDED;
        }
        if (Long.valueOf(-2L).equals(result)) {
            throw new IllegalStateException(
                    "latest-head generation is already bound to a different execution");
        }
        if (Long.valueOf(-3L).equals(result)) {
            throw new IllegalStateException(
                    "latest-head generation was not claimed");
        }
        throw new IllegalStateException("latest-head registration did not complete");
    }

    @Override
    public boolean isLatestHead(
            String publicationScopeId,
            String executionId,
            String headRevision) {
        return executionId.equals(redis.opsForValue().get(
                latestExecutionKey(publicationScopeId)))
                && headRevision.equals(redis.opsForValue().get(
                        latestRevisionKey(publicationScopeId)));
    }

    @Override
    public PublicationReservation tryClaimLatestPublication(
            String publicationScopeId,
            String executionId,
            String headRevision,
            String publicationClaimId) {
        Long result = redis.execute(
                CLAIM_LATEST_PUBLICATION,
                List.of(
                        latestExecutionKey(publicationScopeId),
                        latestRevisionKey(publicationScopeId),
                        publicationClaimKey(publicationScopeId, publicationClaimId)),
                executionId,
                headRevision);
        if (Long.valueOf(1L).equals(result)) {
            return PublicationReservation.RESERVED;
        }
        if (Long.valueOf(0L).equals(result)) {
            return PublicationReservation.DUPLICATE;
        }
        return PublicationReservation.STALE_HEAD;
    }

    @Override
    public String activateCandidateKillSwitch(String receiptJson) {
        Objects.requireNonNull(receiptJson, "receiptJson");
        if (receiptJson.isBlank()) {
            throw new IllegalArgumentException("rollback receipt is required");
        }
        redis.opsForValue().setIfAbsent(
                CANDIDATE_KILL_SWITCH_KEY, receiptJson);
        String persisted = redis.opsForValue().get(
                CANDIDATE_KILL_SWITCH_KEY);
        if (persisted == null) {
            throw new IllegalStateException(
                    "candidate rollback claim exists without a receipt");
        }
        return persisted;
    }

    @Override
    public Optional<String> findCandidateKillSwitchReceipt() {
        return Optional.ofNullable(redis.opsForValue().get(
                CANDIDATE_KILL_SWITCH_KEY));
    }

    private String planKey(String executionId) {
        return PREFIX + "plan:" + sha256(executionId);
    }

    private String artifactKey(String executionId, ArtifactNamespace namespace) {
        String partition = namespace == ArtifactNamespace.SHADOW
                ? "shadow-artifact:"
                : "primary-artifact:";
        return PREFIX + partition + sha256(executionId);
    }

    private String artifactValueKey(
            String executionId,
            ArtifactNamespace namespace,
            String artifactId) {
        return PREFIX + "artifact-value:"
                + sha256(executionId + '\0' + namespace.name() + '\0' + artifactId);
    }

    private String latestExecutionKey(String publicationScopeId) {
        return PREFIX + redisSlot(publicationScopeId) + ":latest-execution";
    }

    private String generationCounterKey(String publicationScopeId) {
        return PREFIX + redisSlot(publicationScopeId) + ":generation-counter";
    }

    private String admissionGenerationKey(
            String publicationScopeId,
            String admissionId) {
        return PREFIX + redisSlot(publicationScopeId)
                + ":admission-generation:" + sha256(admissionId);
    }

    private String generationClaimKey(String publicationScopeId) {
        return PREFIX + redisSlot(publicationScopeId) + ":generation-claims";
    }

    private String latestGenerationKey(String publicationScopeId) {
        return PREFIX + redisSlot(publicationScopeId) + ":latest-generation";
    }

    private String latestRevisionKey(String publicationScopeId) {
        return PREFIX + redisSlot(publicationScopeId) + ":latest-revision";
    }

    private String publicationClaimKey(
            String publicationScopeId,
            String publicationClaimId) {
        return PREFIX + redisSlot(publicationScopeId)
                + ":publication-claim:" + publicationClaimId;
    }

    private String redisSlot(String publicationScopeId) {
        return "{pr-" + publicationScopeId + '}';
    }

    private String write(Object value) {
        try {
            return objectMapper.writeValueAsString(value);
        } catch (JsonProcessingException error) {
            throw new IllegalStateException("execution control value is not serializable", error);
        }
    }

    private <T> T read(String json, Class<T> type) {
        try {
            return objectMapper.readValue(json, type);
        } catch (JsonProcessingException error) {
            throw new IllegalStateException("persisted execution control value is invalid", error);
        }
    }

    private String sha256(String value) {
        return PolicyHashing.sha256(value);
    }
}
