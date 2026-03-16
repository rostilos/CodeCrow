package org.rostilos.codecrow.queue;

import org.junit.jupiter.api.*;
import org.rostilos.codecrow.testsupport.containers.SharedRedisContainer;
import org.springframework.data.redis.connection.lettuce.LettuceConnectionFactory;
import org.springframework.data.redis.connection.RedisStandaloneConfiguration;
import org.springframework.data.redis.core.StringRedisTemplate;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Verifies that different Redis DB indices do not cross-contaminate.
 */
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
class QueueIsolationIT {

    @Test
    @Order(1)
    void shouldIsolateBetweenDatabases() {
        var container = SharedRedisContainer.getInstance();

        // DB 1 — queue
        RedisStandaloneConfiguration config1 = new RedisStandaloneConfiguration(
                container.getHost(), container.getMappedPort(6379));
        config1.setDatabase(1);
        LettuceConnectionFactory factory1 = new LettuceConnectionFactory(config1);
        factory1.afterPropertiesSet();
        StringRedisTemplate template1 = new StringRedisTemplate(factory1);

        // DB 2 — different
        RedisStandaloneConfiguration config2 = new RedisStandaloneConfiguration(
                container.getHost(), container.getMappedPort(6379));
        config2.setDatabase(2);
        LettuceConnectionFactory factory2 = new LettuceConnectionFactory(config2);
        factory2.afterPropertiesSet();
        StringRedisTemplate template2 = new StringRedisTemplate(factory2);

        template1.opsForValue().set("isolation:key", "db1_value");
        template2.opsForValue().set("isolation:key", "db2_value");

        assertThat(template1.opsForValue().get("isolation:key")).isEqualTo("db1_value");
        assertThat(template2.opsForValue().get("isolation:key")).isEqualTo("db2_value");

        // Cleanup
        template1.delete("isolation:key");
        template2.delete("isolation:key");
        factory1.destroy();
        factory2.destroy();
    }
}
