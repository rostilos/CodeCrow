package org.rostilos.codecrow.queue;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.data.redis.core.ListOperations;
import org.springframework.data.redis.core.StringRedisTemplate;

import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class RedisQueueServiceTest {

    @Mock
    private StringRedisTemplate redisTemplate;

    @Mock
    private ListOperations<String, String> listOperations;

    private RedisQueueService service;

    @BeforeEach
    void setUp() {
        lenient().when(redisTemplate.opsForList()).thenReturn(listOperations);
        service = new RedisQueueService(redisTemplate);
    }

    // ── leftPush ─────────────────────────────────────────────────────────

    @Test
    void leftPush_shouldDelegateToRedisListLeftPush() {
        service.leftPush("my-queue", "payload-1");

        verify(redisTemplate).opsForList();
        verify(listOperations).leftPush("my-queue", "payload-1");
    }

    @Test
    void leftPush_withEmptyPayload_shouldStillDelegate() {
        service.leftPush("q", "");

        verify(listOperations).leftPush("q", "");
    }

    @Test
    void leftPush_withLargePayload_shouldDelegate() {
        String largePayload = "x".repeat(100_000);
        service.leftPush("big-queue", largePayload);

        verify(listOperations).leftPush("big-queue", largePayload);
    }

    // ── rightPop ─────────────────────────────────────────────────────────

    @Test
    void rightPop_shouldReturnValueFromRedis() {
        when(listOperations.rightPop("q", 5, TimeUnit.SECONDS)).thenReturn("result");

        String result = service.rightPop("q", 5);

        assertThat(result).isEqualTo("result");
        verify(listOperations).rightPop("q", 5, TimeUnit.SECONDS);
    }

    @Test
    void rightPop_whenEmpty_shouldReturnNull() {
        when(listOperations.rightPop("q", 1, TimeUnit.SECONDS)).thenReturn(null);

        String result = service.rightPop("q", 1);

        assertThat(result).isNull();
    }

    @Test
    void rightPop_withZeroTimeout_shouldDelegate() {
        when(listOperations.rightPop("q", 0, TimeUnit.SECONDS)).thenReturn("item");

        String result = service.rightPop("q", 0);

        assertThat(result).isEqualTo("item");
    }

    // ── setExpiry ────────────────────────────────────────────────────────

    @Test
    void setExpiry_shouldSetKeyTTLInMinutes() {
        service.setExpiry("my-key", 30);

        verify(redisTemplate).expire("my-key", 30, TimeUnit.MINUTES);
    }

    @Test
    void setExpiry_withZeroMinutes_shouldDelegate() {
        service.setExpiry("key", 0);

        verify(redisTemplate).expire("key", 0, TimeUnit.MINUTES);
    }

    // ── deleteKey ────────────────────────────────────────────────────────

    @Test
    void deleteKey_shouldDeleteFromRedis() {
        service.deleteKey("my-key");

        verify(redisTemplate).delete("my-key");
    }

    @Test
    void deleteKey_withEmptyKey_shouldStillDelegate() {
        service.deleteKey("");

        verify(redisTemplate).delete("");
    }
}
