package org.rostilos.codecrow.queue;

import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;

import java.util.concurrent.TimeUnit;

@Service
public class RedisQueueService {

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

    public void setExpiry(String key, long timeoutMinutes) {
        redisTemplate.expire(key, timeoutMinutes, TimeUnit.MINUTES);
    }

    public void deleteKey(String key) {
        redisTemplate.delete(key);
    }
}
