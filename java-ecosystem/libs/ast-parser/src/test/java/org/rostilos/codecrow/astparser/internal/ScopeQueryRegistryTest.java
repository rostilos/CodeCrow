package org.rostilos.codecrow.astparser.internal;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class ScopeQueryRegistryTest {

    @Test
    void empty_plugin_registry_has_no_embedded_scope_queries() {
        assertThat(new ScopeQueryRegistry().preloadAll()).isZero();
    }
}
