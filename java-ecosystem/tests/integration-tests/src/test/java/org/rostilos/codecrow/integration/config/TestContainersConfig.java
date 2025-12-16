package org.rostilos.codecrow.integration.config;

import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.utility.DockerImageName;

/**
 * TestContainers configuration for integration tests.
 * Provides a PostgreSQL container for database tests.
 * 
 * Uses static container initialization to ensure the database is available
 * before Spring context loads.
 */
@TestConfiguration(proxyBeanMethods = false)
public class TestContainersConfig {

    private static final PostgreSQLContainer<?> POSTGRES_CONTAINER;

    static {
        POSTGRES_CONTAINER = new PostgreSQLContainer<>(DockerImageName.parse("postgres:16-alpine"))
                .withDatabaseName("codecrow_test")
                .withUsername("test")
                .withPassword("test")
                .withReuse(true);
        POSTGRES_CONTAINER.start();
        
        // Set system properties early for Spring to pick up
        System.setProperty("spring.datasource.url", POSTGRES_CONTAINER.getJdbcUrl());
        System.setProperty("spring.datasource.username", POSTGRES_CONTAINER.getUsername());
        System.setProperty("spring.datasource.password", POSTGRES_CONTAINER.getPassword());
        System.setProperty("spring.datasource.driver-class-name", "org.postgresql.Driver");
        System.setProperty("spring.jpa.database-platform", "org.hibernate.dialect.PostgreSQLDialect");
    }

    @Bean
    public PostgreSQLContainer<?> postgreSQLContainer() {
        return POSTGRES_CONTAINER;
    }

    public static String getJdbcUrl() {
        return POSTGRES_CONTAINER.getJdbcUrl();
    }

    public static String getUsername() {
        return POSTGRES_CONTAINER.getUsername();
    }

    public static String getPassword() {
        return POSTGRES_CONTAINER.getPassword();
    }
}
