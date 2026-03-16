package org.rostilos.codecrow.testsupport.containers;

import org.testcontainers.containers.PostgreSQLContainer;

/**
 * Shared singleton PostgreSQL container for all integration tests.
 * Uses TC_REUSABLE=true for faster local runs.
 */
public final class SharedPostgresContainer {

    private static final PostgreSQLContainer<?> INSTANCE;

    static {
        INSTANCE = new PostgreSQLContainer<>("postgres:16-alpine")
                .withDatabaseName("codecrow_test")
                .withUsername("codecrow_test")
                .withPassword("codecrow_test")
                .withReuse(true);
        INSTANCE.start();
    }

    private SharedPostgresContainer() {
    }

    public static PostgreSQLContainer<?> getInstance() {
        return INSTANCE;
    }

    /**
     * Apply datasource system properties so Spring picks up the container.
     * Call from a {@link org.springframework.context.ApplicationContextInitializer}.
     */
    public static void applySystemProperties() {
        System.setProperty("spring.datasource.url", INSTANCE.getJdbcUrl());
        System.setProperty("spring.datasource.username", INSTANCE.getUsername());
        System.setProperty("spring.datasource.password", INSTANCE.getPassword());
        System.setProperty("spring.datasource.driver-class-name", "org.postgresql.Driver");
    }
}
