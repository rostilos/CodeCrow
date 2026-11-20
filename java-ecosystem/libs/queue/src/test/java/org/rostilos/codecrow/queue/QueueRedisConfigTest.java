package org.rostilos.codecrow.queue;

import org.junit.jupiter.api.Test;
import org.springframework.boot.autoconfigure.data.redis.RedisReactiveAutoConfiguration;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;
import org.springframework.data.redis.connection.ReactiveRedisConnectionFactory;
import org.springframework.data.redis.core.ReactiveRedisTemplate;
import org.springframework.data.redis.core.StringRedisTemplate;

import java.lang.reflect.Field;

import static org.assertj.core.api.Assertions.assertThat;

class QueueRedisConfigTest {

    @Test
    void queueRemainsTheDefaultForReactiveAutoConfigurationAndUnqualifiedConsumers() {
        try (var context = new AnnotationConfigApplicationContext(
                QueueRedisConfig.class, RedisReactiveAutoConfiguration.class)) {
            var queueFactory = context.getBean("queueRedisConnectionFactory");
            assertThat(context.getBean(ReactiveRedisConnectionFactory.class)).isSameAs(queueFactory);
            assertThat(context.getBean("reactiveRedisTemplate", ReactiveRedisTemplate.class).getConnectionFactory())
                    .isSameAs(queueFactory);
            assertThat(context.getBean(StringRedisTemplate.class)).isSameAs(context.getBean("queueRedisTemplate"));
            assertThat(context.getBean("vcsCooldownRedisTemplate", StringRedisTemplate.class).getConnectionFactory())
                    .isSameAs(context.getBean("vcsCooldownRedisConnectionFactory"));
        }
    }

    @Test
    void cooldownUsesSameDatabaseWithShortTimeoutsWithoutChangingQueue() throws Exception {
        var config = new QueueRedisConfig();
        setField(config, "redisHost", "redis.example.com");
        setField(config, "redisPort", 6380);
        setField(config, "queueDatabase", 5);
        var cooldown = config.vcsCooldownRedisConnectionFactory();
        var queue = config.queueRedisConnectionFactory();
        assertThat(cooldown.getHostName()).isEqualTo(queue.getHostName());
        assertThat(cooldown.getPort()).isEqualTo(queue.getPort());
        assertThat(cooldown.getDatabase()).isEqualTo(5);
        assertThat(cooldown.getClientConfiguration().getCommandTimeout()).isEqualTo(java.time.Duration.ofSeconds(2));
        assertThat(cooldown.getClientConfiguration().getClientOptions().orElseThrow()
                .getSocketOptions().getConnectTimeout()).isEqualTo(java.time.Duration.ofSeconds(2));
        assertThat(queue.getClientConfiguration().getCommandTimeout()).isEqualTo(java.time.Duration.ofSeconds(60));
    }

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
