package org.rostilos.codecrow.testsupport.initializer;

import org.rostilos.codecrow.testsupport.containers.SharedPostgresContainer;
import org.springframework.context.ApplicationContextInitializer;
import org.springframework.context.ConfigurableApplicationContext;

/**
 * Spring context initializer that starts a shared Testcontainers PostgreSQL
 * and injects datasource properties before the context refreshes.
 * <p>
 * Usage: {@code @ContextConfiguration(initializers = PostgresContainerInitializer.class)}
 */
public class PostgresContainerInitializer
        implements ApplicationContextInitializer<ConfigurableApplicationContext> {

    @Override
    public void initialize(ConfigurableApplicationContext ctx) {
        SharedPostgresContainer.applySystemProperties();
    }
}
