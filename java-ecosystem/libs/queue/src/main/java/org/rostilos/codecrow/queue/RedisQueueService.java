package org.rostilos.codecrow.queue;

import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;

import java.util.concurrent.TimeUnit;

@Service
public class RedisQueueService {

    public static final String JOB_OWNER_KEY_PREFIX = "codecrow:inference:owner:";
    public static final long JOB_OWNER_TTL_SECONDS = 60;

    private final StringRedisTemplate redisTemplate;

    public RedisQueueService(@Qualifier("queueRedisTemplate") StringRedisTemplate redisTemplate) {
        this.redisTemplate = redisTemplate;
    }

    public void leftPush(String queueKey, String payload) {
        redisTemplate.opsForList().leftPush(queueKey, payload);
    }

    public String rightPop(String queueKey, long timeoutSeconds) {
        return redisTemplate.opsForList().rightPop(queueKey, timeoutSeconds, TimeUnit.SECONDS);
    }

    public boolean listContains(String queueKey, String payload) {
        return redisTemplate.opsForList().indexOf(queueKey, payload) != null;
    }

    public void removeFromList(String queueKey, String payload) {
        redisTemplate.opsForList().remove(queueKey, 1, payload);
    }

    public boolean hasKey(String key) {
        return Boolean.TRUE.equals(redisTemplate.hasKey(key));
    }

    public void setExpiry(String key, long timeoutMinutes) {
        redisTemplate.expire(key, timeoutMinutes, TimeUnit.MINUTES);
    }

    public void deleteKey(String key) {
        redisTemplate.delete(key);
    }

    public void startJobLease(String key) {
        redisTemplate.opsForValue().set(key, "active", JOB_OWNER_TTL_SECONDS, TimeUnit.SECONDS);
    }

    public boolean renewJobLease(String key) {
        return Boolean.TRUE.equals(redisTemplate.expire(key, JOB_OWNER_TTL_SECONDS, TimeUnit.SECONDS));
    }
}
