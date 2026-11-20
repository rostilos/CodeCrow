package org.rostilos.codecrow.pipelineagent;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.Timeout;
import org.rostilos.codecrow.queue.QueueRedisConfig;
import org.rostilos.codecrow.vcsclient.VcsRateLimitException;
import org.rostilos.codecrow.vcsclient.VcsRateLimits;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.test.util.ReflectionTestUtils;

import java.net.InetAddress;
import java.net.ServerSocket;
import java.time.Duration;
import java.time.Instant;

import static org.assertj.core.api.Assertions.*;

class VcsCooldownOutageIT {
    @Test
    @Timeout(15)
    void stalledStoreFallsBackQuicklyAndKeepsKnownLocalCooldowns() throws Exception {
        // TCP connects, but the store never responds to Redis commands.
        try (var stalled = new ServerSocket(0, 10, InetAddress.getLoopbackAddress())) {
            var config = new QueueRedisConfig();
            ReflectionTestUtils.setField(config, "redisHost", stalled.getInetAddress().getHostAddress());
            ReflectionTestUtils.setField(config, "redisPort", stalled.getLocalPort());
            ReflectionTestUtils.setField(config, "queueDatabase", 1);
            var factory = config.vcsCooldownRedisConnectionFactory();
            factory.afterPropertiesSet();
            try {
                var limits = new VcsRateLimits();
                limits.useRedis(new StringRedisTemplate(factory));
                long started = System.nanoTime();
                limits.check("unlimited", "core");
                assertThat(Duration.ofNanos(System.nanoTime() - started)).isLessThan(Duration.ofSeconds(6));

                started = System.nanoTime();
                limits.check("another-installation", "core");
                var retryAt = Instant.now().plusSeconds(300);
                limits.remember("limited", new VcsRateLimitException(429, "core", retryAt, "fixture", false));
                assertThatThrownBy(() -> limits.check("limited", "core"))
                        .isInstanceOfSatisfying(VcsRateLimitException.class,
                                failure -> assertThat(failure.retryAt()).isEqualTo(retryAt));
                assertThat(Duration.ofNanos(System.nanoTime() - started)).isLessThan(Duration.ofSeconds(1));
            } finally {
                factory.destroy();
            }
        }
    }
}
