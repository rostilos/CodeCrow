package org.rostilos.codecrow.queue;

import io.lettuce.core.ClientOptions;
import io.lettuce.core.SocketOptions;
import java.time.Duration;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Primary;
import org.springframework.data.redis.connection.RedisStandaloneConfiguration;
import org.springframework.data.redis.connection.lettuce.LettuceConnectionFactory;
import org.springframework.data.redis.connection.lettuce.LettuceClientConfiguration;
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
    @Primary
    public LettuceConnectionFactory queueRedisConnectionFactory() {
        return new LettuceConnectionFactory(redisConfiguration());
    }

    private RedisStandaloneConfiguration redisConfiguration() {
        RedisStandaloneConfiguration config = new RedisStandaloneConfiguration();
        config.setHostName(redisHost);
        config.setPort(redisPort);
        config.setDatabase(queueDatabase);
        return config;
    }

    @Bean("queueRedisTemplate")
    @Primary
    public StringRedisTemplate queueRedisTemplate() {
        return new StringRedisTemplate(queueRedisConnectionFactory());
    }

    // Cooldown probes must not inherit the queue's long blocking-operation timeout.
    @Bean("vcsCooldownRedisConnectionFactory")
    public LettuceConnectionFactory vcsCooldownRedisConnectionFactory() {
        var timeout = Duration.ofSeconds(2);
        var client = LettuceClientConfiguration.builder().commandTimeout(timeout)
                .clientOptions(ClientOptions.builder().socketOptions(
                        SocketOptions.builder().connectTimeout(timeout).build()).build()).build();
        return new LettuceConnectionFactory(redisConfiguration(), client);
    }

    @Bean("vcsCooldownRedisTemplate")
    public StringRedisTemplate vcsCooldownRedisTemplate() {
        return new StringRedisTemplate(vcsCooldownRedisConnectionFactory());
    }
}
