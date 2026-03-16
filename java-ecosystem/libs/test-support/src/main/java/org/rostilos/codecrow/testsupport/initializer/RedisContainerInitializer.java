package org.rostilos.codecrow.testsupport.initializer;

import org.rostilos.codecrow.testsupport.containers.SharedRedisContainer;
import org.springframework.context.ApplicationContextInitializer;
import org.springframework.context.ConfigurableApplicationContext;

/**
 * Spring context initializer that starts a shared Testcontainers Redis
 * and injects connection properties before the context refreshes.
 */
public class RedisContainerInitializer
        implements ApplicationContextInitializer<ConfigurableApplicationContext> {

    @Override
    public void initialize(ConfigurableApplicationContext ctx) {
        SharedRedisContainer.applySystemProperties();
    }
}
