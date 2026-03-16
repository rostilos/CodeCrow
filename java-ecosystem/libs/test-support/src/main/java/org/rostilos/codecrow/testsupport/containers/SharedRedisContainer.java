package org.rostilos.codecrow.testsupport.containers;

import org.testcontainers.containers.GenericContainer;
import org.testcontainers.utility.DockerImageName;

/**
 * Shared singleton Redis container for queue integration tests.
 */
public final class SharedRedisContainer {

    private static final GenericContainer<?> INSTANCE;

    static {
        INSTANCE = new GenericContainer<>(DockerImageName.parse("redis:7-alpine"))
                .withExposedPorts(6379)
                .withReuse(true);
        INSTANCE.start();
    }

    private SharedRedisContainer() {
    }

    public static GenericContainer<?> getInstance() {
        return INSTANCE;
    }

    public static String getHost() {
        return INSTANCE.getHost();
    }

    public static int getPort() {
        return INSTANCE.getMappedPort(6379);
    }

    /**
     * Apply Redis connection properties so Spring Data Redis picks up the container.
     */
    public static void applySystemProperties() {
        System.setProperty("spring.redis.host", getHost());
        System.setProperty("spring.redis.port", String.valueOf(getPort()));
    }
}
