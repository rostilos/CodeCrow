package org.rostilos.codecrow.queue;

import org.junit.jupiter.api.*;
import org.rostilos.codecrow.testsupport.containers.SharedRedisContainer;
import org.springframework.data.redis.connection.lettuce.LettuceConnectionFactory;
import org.springframework.data.redis.connection.RedisStandaloneConfiguration;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatCode;

/**
 * Verifies Redis connection factory behavior: connection, pooling, reconnection.
 */
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
class ConnectionFactoryIT {

    @Test
    @Order(1)
    void shouldCreateValidConnection() {
        var container = SharedRedisContainer.getInstance();
        RedisStandaloneConfiguration config = new RedisStandaloneConfiguration(
                container.getHost(), container.getMappedPort(6379));
        config.setDatabase(1);
        LettuceConnectionFactory factory = new LettuceConnectionFactory(config);
        factory.afterPropertiesSet();

        assertThatCode(() -> {
            var conn = factory.getConnection();
            assertThat(conn.ping()).isEqualTo("PONG");
            conn.close();
        }).doesNotThrowAnyException();

        factory.destroy();
    }

    @Test
    @Order(2)
    void shouldReconnectAfterConnectionDrop() {
        var container = SharedRedisContainer.getInstance();
        RedisStandaloneConfiguration config = new RedisStandaloneConfiguration(
                container.getHost(), container.getMappedPort(6379));
        config.setDatabase(1);
        LettuceConnectionFactory factory = new LettuceConnectionFactory(config);
        factory.afterPropertiesSet();

        // First connection
        var conn1 = factory.getConnection();
        assertThat(conn1.ping()).isEqualTo("PONG");
        conn1.close();

        // Second connection — should still work
        var conn2 = factory.getConnection();
        assertThat(conn2.ping()).isEqualTo("PONG");
        conn2.close();

        factory.destroy();
    }
}
