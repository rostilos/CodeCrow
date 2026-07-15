package org.rostilos.codecrow.analysisengine.policy;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.List;
import java.util.Objects;
import java.util.Optional;

/**
 * Redis-backed scaffold with distinct plan, primary-artifact, shadow-artifact,
 * and publication-claim namespaces. Values intentionally have no rollout-time
 * deletion path; retention is introduced by P1-11.
 */
@Service
public class RedisExecutionControlStore implements ExecutionControlStore {
    static final String PREFIX = "codecrow:llm-handoff:policy:v1:";

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

    private String planKey(String executionId) {
        return PREFIX + "plan:" + sha256(executionId);
    }

    private String artifactKey(String executionId, ArtifactNamespace namespace) {
        String partition = namespace == ArtifactNamespace.SHADOW
                ? "shadow-artifact:"
                : "primary-artifact:";
        return PREFIX + partition + sha256(executionId);
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
