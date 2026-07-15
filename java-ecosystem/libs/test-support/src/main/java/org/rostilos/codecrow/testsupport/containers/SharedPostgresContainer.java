package org.rostilos.codecrow.testsupport.containers;

import org.rostilos.codecrow.testsupport.legacy.LegacyContainerEndpoints;
import org.rostilos.codecrow.testsupport.legacy.LegacyContainerItSession;

import java.util.List;

/** Facade for the fixed PostgreSQL endpoint provisioned outside this JVM. */
public final class SharedPostgresContainer {

    private SharedPostgresContainer() {
    }

    public static LegacyContainerEndpoints.PostgresEndpoint getInstance() {
        return LegacyContainerItSession.requirePostgresEndpoint();
    }

    public static List<String> springProperties() {
        return getInstance().springProperties();
    }
}
