package org.rostilos.codecrow.testsupport.containers;

import org.rostilos.codecrow.testsupport.legacy.LegacyContainerEndpoints;
import org.rostilos.codecrow.testsupport.legacy.LegacyContainerItSession;

import java.util.List;

/** Facade for the fixed Redis endpoint provisioned outside this JVM. */
public final class SharedRedisContainer {

    private SharedRedisContainer() {
    }

    public static LegacyContainerEndpoints.RedisEndpoint getInstance() {
        return LegacyContainerItSession.requireRedisEndpoint();
    }

    public static String getHost() {
        return getInstance().getHost();
    }

    public static int getPort() {
        return getInstance().getMappedPort(6379);
    }

    public static List<String> springProperties() {
        return getInstance().springProperties();
    }
}
