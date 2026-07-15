package org.rostilos.codecrow.testsupport.initializer;

import org.rostilos.codecrow.testsupport.containers.SharedPostgresContainer;
import org.springframework.boot.test.util.TestPropertyValues;
import org.springframework.context.ApplicationContextInitializer;
import org.springframework.context.ConfigurableApplicationContext;

/**
 * Injects the reviewed external PostgreSQL endpoint before context refresh.
 * <p>
 * Usage: {@code @ContextConfiguration(initializers = PostgresContainerInitializer.class)}
 */
public class PostgresContainerInitializer
        implements ApplicationContextInitializer<ConfigurableApplicationContext> {

    @Override
    public void initialize(ConfigurableApplicationContext ctx) {
        TestPropertyValues.of(SharedPostgresContainer.springProperties()).applyTo(ctx);
    }
}
