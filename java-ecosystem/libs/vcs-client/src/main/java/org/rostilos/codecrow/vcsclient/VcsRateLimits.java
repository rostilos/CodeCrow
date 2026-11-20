package org.rostilos.codecrow.vcsclient;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Component;

import java.time.Clock;
import java.time.Instant;
import java.util.List;
import java.util.concurrent.ConcurrentHashMap;

/** Shares only provider cooldown timestamps, never repository content or job results. */
@Component
public class VcsRateLimits {
    private static final Logger log = LoggerFactory.getLogger(VcsRateLimits.class);
    private static final DefaultRedisScript<Long> EXTEND = new DefaultRedisScript<>("""
            local previous = tonumber(redis.call('GET', KEYS[1]) or '0')
            local deadline = math.max(previous, tonumber(ARGV[1]))
            redis.call('SET', KEYS[1], tostring(deadline), 'PXAT', deadline)
            return deadline
            """, Long.class);
    private final ConcurrentHashMap<String, Instant> local = new ConcurrentHashMap<>();
    private final Clock clock;
    private StringRedisTemplate redis;
    private volatile Instant redisUnavailableUntil = Instant.EPOCH;

    public VcsRateLimits() { this(null, Clock.systemUTC()); }

    VcsRateLimits(StringRedisTemplate redis, Clock clock) {
        this.redis = redis;
        this.clock = clock;
    }

    @Autowired(required = false)
    public void useRedis(@Qualifier("vcsCooldownRedisTemplate") StringRedisTemplate redis) {
        this.redis = redis;
    }

    public void check(String identity, String resource) throws VcsRateLimitException {
        Instant now = clock.instant();
        List<String> keys = List.of(key(identity, "*"), key(identity, resource));
        for (int i = 0; i < keys.size(); i++) {
            Instant until = local.get(keys.get(i));
            if (until != null && until.isAfter(now)) {
                throw new VcsRateLimitException(429, resource, until, null, i == 0);
            }
        }
        if (redis != null && !now.isBefore(redisUnavailableUntil)) {
            try {
                List<String> values = redis.opsForValue().multiGet(keys);
                if (values != null) {
                    for (int i = 0; i < values.size(); i++) {
                        if (values.get(i) == null) continue;
                        Instant until = Instant.ofEpochMilli(Long.parseLong(values.get(i)));
                        if (until.isAfter(now)) {
                            local.merge(keys.get(i), until, VcsRateLimits::later);
                            throw new VcsRateLimitException(429, resource, until, null, i == 0);
                        }
                    }
                }
            } catch (RuntimeException unavailable) {
                redisUnavailable();
            }
        }
    }

    public void remember(String identity, VcsRateLimitException failure) {
        Instant now = clock.instant();
        String key = key(identity, failure.sharedAcrossResources() ? "*" : failure.resource());
        if (!failure.retryAt().isAfter(now)) return;
        local.entrySet().removeIf(entry -> !entry.getValue().isAfter(now));
        local.merge(key, failure.retryAt(), VcsRateLimits::later);
        if (redis != null && !now.isBefore(redisUnavailableUntil)) {
            try {
                Long shared = redis.execute(EXTEND, List.of(key), Long.toString(failure.retryAt().toEpochMilli()));
                if (shared != null) local.merge(key, Instant.ofEpochMilli(shared), VcsRateLimits::later);
            } catch (RuntimeException unavailable) {
                redisUnavailable();
            }
        }
    }

    private void redisUnavailable() {
        redisUnavailableUntil = clock.instant().plusSeconds(30);
        log.warn("Shared VCS cooldown store unavailable; retaining process-local provider cooldowns for 30 seconds");
    }

    private static Instant later(Instant first, Instant second) { return first.isAfter(second) ? first : second; }
    private static String key(String identity, String resource) { return "codecrow:vcs-cooldown:" + identity + ":" + resource; }
}
