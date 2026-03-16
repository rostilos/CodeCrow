package org.rostilos.codecrow.queue;

import org.junit.jupiter.api.*;
import org.rostilos.codecrow.testsupport.containers.SharedRedisContainer;
import org.springframework.data.redis.connection.lettuce.LettuceConnectionFactory;
import org.springframework.data.redis.connection.RedisStandaloneConfiguration;
import org.springframework.data.redis.core.StringRedisTemplate;

import java.time.Duration;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;

@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
class RedisQueueIT {

    private static RedisQueueService queueService;
    private static StringRedisTemplate redisTemplate;

    @BeforeAll
    static void setup() {
        var container = SharedRedisContainer.getInstance();
        RedisStandaloneConfiguration config = new RedisStandaloneConfiguration(
                container.getHost(), container.getMappedPort(6379));
        config.setDatabase(1);
        LettuceConnectionFactory factory = new LettuceConnectionFactory(config);
        factory.afterPropertiesSet();
        redisTemplate = new StringRedisTemplate(factory);
        queueService = new RedisQueueService(redisTemplate);
    }

    @BeforeEach
    void cleanQueue() {
        try {
            redisTemplate.delete("test:queue");
            redisTemplate.delete("test:expiry");
            redisTemplate.delete("test:isolation:a");
            redisTemplate.delete("test:isolation:b");
        } catch (Exception ignored) {
        }
    }

    @Test
    @Order(1)
    void shouldPushAndPopFromQueue() {
        queueService.leftPush("test:queue", "{\"event\":\"push\"}");
        String value = queueService.rightPop("test:queue", 2);

        assertThat(value).isNotNull();
        assertThat(value).contains("push");
    }

    @Test
    @Order(2)
    void shouldReturnNullOnEmptyQueue() {
        String value = queueService.rightPop("test:queue", 1);
        assertThat(value).isNull();
    }

    @Test
    @Order(3)
    void shouldMaintainFIFOOrder() {
        queueService.leftPush("test:queue", "first");
        queueService.leftPush("test:queue", "second");
        queueService.leftPush("test:queue", "third");

        assertThat(queueService.rightPop("test:queue", 1)).isEqualTo("first");
        assertThat(queueService.rightPop("test:queue", 1)).isEqualTo("second");
        assertThat(queueService.rightPop("test:queue", 1)).isEqualTo("third");
    }

    @Test
    @Order(4)
    void shouldDeleteKey() {
        queueService.leftPush("test:queue", "deleteme");
        queueService.deleteKey("test:queue");

        String value = queueService.rightPop("test:queue", 1);
        assertThat(value).isNull();
    }

    @Test
    @Order(5)
    void shouldSetExpiry() throws Exception {
        redisTemplate.opsForValue().set("test:expiry", "willexpire");
        queueService.setExpiry("test:expiry", 1); // 1 minute

        Long ttl = redisTemplate.getExpire("test:expiry", TimeUnit.SECONDS);
        assertThat(ttl).isNotNull().isGreaterThan(0).isLessThanOrEqualTo(60);
    }

    @Test
    @Order(6)
    void shouldIsolateQueues() {
        queueService.leftPush("test:isolation:a", "from_a");
        queueService.leftPush("test:isolation:b", "from_b");

        assertThat(queueService.rightPop("test:isolation:a", 1)).isEqualTo("from_a");
        assertThat(queueService.rightPop("test:isolation:b", 1)).isEqualTo("from_b");
        // Cross-contamination check
        assertThat(queueService.rightPop("test:isolation:a", 1)).isNull();
        assertThat(queueService.rightPop("test:isolation:b", 1)).isNull();
    }

    @Test
    @Order(7)
    void shouldHandleLargePayload() {
        String largePayload = "x".repeat(100_000);
        queueService.leftPush("test:queue", largePayload);

        String popped = queueService.rightPop("test:queue", 2);
        assertThat(popped).hasSize(100_000);
    }

    @Test
    @Order(8)
    void shouldHandleConcurrentPushPop() throws Exception {
        int count = 100;
        for (int i = 0; i < count; i++) {
            queueService.leftPush("test:queue", "item_" + i);
        }

        int popped = 0;
        while (queueService.rightPop("test:queue", 1) != null) {
            popped++;
        }
        assertThat(popped).isEqualTo(count);
    }
}
