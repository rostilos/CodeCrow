package org.rostilos.codecrow.queue;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.data.redis.connection.RedisStandaloneConfiguration;
import org.springframework.data.redis.connection.lettuce.LettuceConnectionFactory;
import org.springframework.data.redis.core.StringRedisTemplate;

/**
 * Dedicated Redis configuration for the async queue.
 * Uses a separate Redis database (default: DB 1) to isolate queue data
 * from Spring Session data (DB 0), preventing key collisions and
 * allowing independent eviction/flush policies.
 */
@Configuration
public class QueueRedisConfig {

    @Value("${spring.redis.host:localhost}")
    private String redisHost;

    @Value("${spring.redis.port:6379}")
    private int redisPort;

    @Value("${codecrow.queue.redis.database:1}")
    private int queueDatabase;

    @Bean("queueRedisConnectionFactory")
    public LettuceConnectionFactory queueRedisConnectionFactory() {
        RedisStandaloneConfiguration config = new RedisStandaloneConfiguration();
        config.setHostName(redisHost);
        config.setPort(redisPort);
        config.setDatabase(queueDatabase);
        return new LettuceConnectionFactory(config);
    }

    @Bean("queueRedisTemplate")
    public StringRedisTemplate queueRedisTemplate() {
        return new StringRedisTemplate(queueRedisConnectionFactory());
    }
}
