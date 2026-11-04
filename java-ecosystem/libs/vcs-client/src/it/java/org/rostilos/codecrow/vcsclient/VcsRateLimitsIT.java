package org.rostilos.codecrow.vcsclient;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.testsupport.containers.SharedRedisContainer;
import org.springframework.data.redis.connection.RedisStandaloneConfiguration;
import org.springframework.data.redis.connection.lettuce.LettuceConnectionFactory;
import org.springframework.data.redis.core.StringRedisTemplate;

import java.time.Clock;
import java.time.Instant;
import java.util.UUID;
import java.util.concurrent.Executors;

import static org.assertj.core.api.Assertions.*;

class VcsRateLimitsIT {
    @Test
    void sharedCooldownSurvivesWorkerReplacementAndConcurrentUpdatesCannotShortenIt() throws Exception {
        var container = SharedRedisContainer.getInstance();
        var config = new RedisStandaloneConfiguration(container.getHost(), container.getMappedPort(6379));
        config.setDatabase(1);
        var factory = new LettuceConnectionFactory(config);
        factory.afterPropertiesSet();
        var redis = new StringRedisTemplate(factory);
        String identity = "rate-limit-it-" + UUID.randomUUID();
        Instant longest = Instant.now().plusSeconds(600);
        var workers = Executors.newFixedThreadPool(4);
        try {
            var first = new VcsRateLimits(redis, Clock.systemUTC());
            var second = new VcsRateLimits(redis, Clock.systemUTC());
            var longWrite = workers.submit(() -> first.remember(identity,
                    new VcsRateLimitException(403, "core", longest, "req-long", false)));
            var shortWrite = workers.submit(() -> second.remember(identity,
                    new VcsRateLimitException(403, "core", longest.minusSeconds(300), "req-short", false)));
            longWrite.get();
            shortWrite.get();
            var replacement = new VcsRateLimits(redis, Clock.systemUTC());
            assertThatThrownBy(() -> replacement.check(identity, "core"))
                    .isInstanceOfSatisfying(VcsRateLimitException.class,
                            limited -> assertThat(limited.retryAt().toEpochMilli()).isEqualTo(longest.toEpochMilli()));
            replacement.check(identity, "graphql");
            replacement.check(identity + "-different-tenant", "core");
            assertThat(redis.getExpire("codecrow:vcs-cooldown:" + identity + ":core")).isPositive();
        } finally {
            workers.shutdownNow();
            redis.delete("codecrow:vcs-cooldown:" + identity + ":core");
            factory.destroy();
        }
    }
}
