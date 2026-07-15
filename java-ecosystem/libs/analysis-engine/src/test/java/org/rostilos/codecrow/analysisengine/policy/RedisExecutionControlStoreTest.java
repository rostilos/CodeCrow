package org.rostilos.codecrow.analysisengine.policy;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.data.redis.core.ListOperations;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.ValueOperations;

import java.time.Instant;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class RedisExecutionControlStoreTest {
    private StringRedisTemplate redis;
    private ValueOperations<String, String> valueOperations;
    private ListOperations<String, String> listOperations;
    private Map<String, String> values;
    private Map<String, List<String>> lists;
    private RedisExecutionControlStore store;

    @BeforeEach
    void setUp() {
        redis = mock(StringRedisTemplate.class);
        valueOperations = mock(ValueOperations.class);
        listOperations = mock(ListOperations.class);
        values = new HashMap<>();
        lists = new HashMap<>();
        when(redis.opsForValue()).thenReturn(valueOperations);
        when(redis.opsForList()).thenReturn(listOperations);
        when(valueOperations.get(anyString())).thenAnswer(call -> values.get(call.getArgument(0)));
        when(valueOperations.setIfAbsent(anyString(), anyString())).thenAnswer(call -> {
            String key = call.getArgument(0);
            String value = call.getArgument(1);
            return values.putIfAbsent(key, value) == null;
        });
        org.mockito.Mockito.doAnswer(call -> {
            String key = call.getArgument(0);
            String value = call.getArgument(1);
            lists.computeIfAbsent(key, ignored -> new ArrayList<>()).add(value);
            return null;
        }).when(listOperations).rightPush(anyString(), anyString());
        when(listOperations.range(anyString(), org.mockito.ArgumentMatchers.anyLong(),
                org.mockito.ArgumentMatchers.anyLong())).thenAnswer(call ->
                List.copyOf(lists.getOrDefault(call.getArgument(0), List.of())));
        ObjectMapper mapper = new ObjectMapper().registerModule(new JavaTimeModule());
        store = new RedisExecutionControlStore(redis, mapper);
    }

    @Test
    void atomicallyKeepsFirstFrozenPlanAcrossRestartStyleReads() {
        FrozenExecutionPlan first = plan("execution-one", "flags-1");
        FrozenExecutionPlan conflicting = plan("execution-one", "flags-2");

        assertThat(store.createPlanIfAbsent(first)).isEqualTo(first);
        assertThat(store.createPlanIfAbsent(conflicting)).isEqualTo(first);
        assertThat(store.findPlan("execution-one")).contains(first);
    }

    @Test
    void usesDifferentRedisPartitionsForPrimaryAndShadowArtifacts() {
        Instant now = Instant.parse("2026-07-14T12:00:00Z");
        ExecutionArtifact primary = new ExecutionArtifact(
                "execution-one", ArtifactNamespace.PRIMARY, "result", "{}", now);
        ExecutionArtifact shadow = new ExecutionArtifact(
                "execution-one:shadow", ArtifactNamespace.SHADOW, "result", "{}", now);

        store.persistArtifact(primary);
        store.persistArtifact(shadow);

        assertThat(store.findArtifacts("execution-one", ArtifactNamespace.PRIMARY))
                .containsExactly(primary);
        assertThat(store.findArtifacts("execution-one:shadow", ArtifactNamespace.SHADOW))
                .containsExactly(shadow);
        assertThat(lists.keySet())
                .anyMatch(key -> key.contains("primary-artifact:"))
                .anyMatch(key -> key.contains("shadow-artifact:"));
    }

    @Test
    void publicationClaimIsAtomic() {
        assertThat(store.tryClaimPublication("d".repeat(64))).isTrue();
        assertThat(store.tryClaimPublication("d".repeat(64))).isFalse();
    }

    @Test
    void emptyPartitionsReturnEmptyImmutableResults() {
        assertThat(store.findPlan("missing-execution")).isEmpty();
        when(listOperations.range(anyString(), org.mockito.ArgumentMatchers.anyLong(),
                org.mockito.ArgumentMatchers.anyLong())).thenReturn(null);

        assertThat(store.findArtifacts("missing-execution", ArtifactNamespace.PRIMARY))
                .isEmpty();
    }

    @Test
    void failsClosedWhenAPlanClaimHasNoPersistedValue() {
        when(valueOperations.setIfAbsent(anyString(), anyString())).thenReturn(false);
        when(valueOperations.get(anyString())).thenReturn(null);

        assertThatThrownBy(() -> store.createPlanIfAbsent(plan("execution-one", "flags-1")))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("claim exists without a value");
    }

    @Test
    void failsClosedForMalformedPersistedJson() {
        when(valueOperations.get(anyString())).thenReturn("not-json");

        assertThatThrownBy(() -> store.findPlan("execution-one"))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("persisted execution control value is invalid");
    }

    @Test
    void failsClosedWhenAnExecutionControlValueCannotBeSerialized() throws Exception {
        ObjectMapper brokenMapper = mock(ObjectMapper.class);
        when(brokenMapper.writeValueAsString(any())).thenThrow(
                new com.fasterxml.jackson.core.JsonProcessingException("broken") { });
        RedisExecutionControlStore brokenStore = new RedisExecutionControlStore(redis, brokenMapper);

        assertThatThrownBy(() -> brokenStore.createPlanIfAbsent(plan("execution-one", "flags-1")))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("not serializable");
    }

    @Test
    void treatsNullRedisPublicationClaimResultAsDenied() {
        when(valueOperations.setIfAbsent(anyString(), anyString())).thenReturn(null);

        assertThat(store.tryClaimPublication("e".repeat(64))).isFalse();
    }

    @Test
    void requiresRedisAndMapperDependencies() {
        ObjectMapper mapper = new ObjectMapper();
        assertThatThrownBy(() -> new RedisExecutionControlStore(null, mapper))
                .isInstanceOf(NullPointerException.class);
        assertThatThrownBy(() -> new RedisExecutionControlStore(redis, null))
                .isInstanceOf(NullPointerException.class);
    }

    private FrozenExecutionPlan plan(String executionId, String revision) {
        Instant now = Instant.parse("2026-07-14T12:00:00Z");
        PolicyExecution primary = new PolicyExecution(
                executionId,
                "legacy-review-v1",
                ExecutionMode.LEGACY,
                PolicySelectionReason.LEGACY_CONFIGURED,
                123,
                true,
                now);
        return new FrozenExecutionPlan(
                executionId,
                revision,
                "a".repeat(64),
                primary,
                null,
                now);
    }
}
