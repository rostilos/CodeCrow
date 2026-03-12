package org.rostilos.codecrow.queue;

import org.junit.jupiter.api.Test;

import java.lang.reflect.Field;

import static org.assertj.core.api.Assertions.assertThat;

class QueueRedisConfigTest {

    private void setField(QueueRedisConfig config, String fieldName, Object value) throws Exception {
        Field field = QueueRedisConfig.class.getDeclaredField(fieldName);
        field.setAccessible(true);
        field.set(config, value);
    }

    @Test
    void queueRedisConnectionFactory_shouldCreateLettuceFactory() throws Exception {
        QueueRedisConfig config = new QueueRedisConfig();
        setField(config, "redisHost", "localhost");
        setField(config, "redisPort", 6379);
        setField(config, "queueDatabase", 1);

        var factory = config.queueRedisConnectionFactory();

        assertThat(factory).isNotNull();
        assertThat(factory.getDatabase()).isEqualTo(1);
        assertThat(factory.getHostName()).isEqualTo("localhost");
        assertThat(factory.getPort()).isEqualTo(6379);
    }

    @Test
    void queueRedisConnectionFactory_withCustomDatabase_shouldUseCustomDb() throws Exception {
        QueueRedisConfig config = new QueueRedisConfig();
        setField(config, "redisHost", "redis.example.com");
        setField(config, "redisPort", 6380);
        setField(config, "queueDatabase", 5);

        var factory = config.queueRedisConnectionFactory();

        assertThat(factory.getDatabase()).isEqualTo(5);
        assertThat(factory.getHostName()).isEqualTo("redis.example.com");
        assertThat(factory.getPort()).isEqualTo(6380);
    }

    @Test
    void queueRedisTemplate_shouldCreateStringRedisTemplate() throws Exception {
        QueueRedisConfig config = new QueueRedisConfig();
        setField(config, "redisHost", "localhost");
        setField(config, "redisPort", 6379);
        setField(config, "queueDatabase", 1);

        var template = config.queueRedisTemplate();

        assertThat(template).isNotNull();
    }
}
