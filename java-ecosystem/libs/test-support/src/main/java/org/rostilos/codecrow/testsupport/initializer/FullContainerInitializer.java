package org.rostilos.codecrow.testsupport.initializer;

/**
 * Combines context-local PostgreSQL and Redis endpoint injection.
 */
public class FullContainerInitializer extends PostgresContainerInitializer {

    @Override
    public void initialize(org.springframework.context.ConfigurableApplicationContext ctx) {
        throw new IllegalStateException(
                "combined PostgreSQL and Redis initialization is not a valid guarded lane"
        );
    }
}
