package org.rostilos.codecrow.testsupport.containers;

import java.util.List;

/**
 * Compatibility sentinel for an unused core-local integration fixture.
 * Core cannot depend on test-support without a cycle, so it must never bypass the guarded
 * launcher/session contract by reading endpoint variables directly.
 */
public final class SharedPostgresContainer {

    private SharedPostgresContainer() {
    }

    public static List<String> springProperties() {
        throw new IllegalStateException(
                "core-local container initialization is disabled; use the listener-guarded "
                        + "test-support integration lane"
        );
    }
}
