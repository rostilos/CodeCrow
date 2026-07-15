package org.rostilos.codecrow.testsupport.initializer;

import org.rostilos.codecrow.testsupport.containers.SharedRedisContainer;
import org.springframework.boot.test.util.TestPropertyValues;
import org.springframework.context.ApplicationContextInitializer;
import org.springframework.context.ConfigurableApplicationContext;

/**
 * Injects the reviewed external Redis endpoint before context refresh.
 */
public class RedisContainerInitializer
        implements ApplicationContextInitializer<ConfigurableApplicationContext> {

    @Override
    public void initialize(ConfigurableApplicationContext ctx) {
        TestPropertyValues.of(SharedRedisContainer.springProperties()).applyTo(ctx);
    }
}
