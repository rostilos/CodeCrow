package org.rostilos.codecrow.plugins.fastapi;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.plugins.PluginCapability;
import org.rostilos.codecrow.plugins.PluginKind;

import static org.assertj.core.api.Assertions.assertThat;

class FastApiPluginTest {
    @Test
    void packagedManifestLoadsThroughTheJavaContract() {
        var descriptor = new FastApiPlugin().descriptor();
        assertThat(descriptor.id()).isEqualTo("fastapi");
        assertThat(descriptor.kind()).isEqualTo(PluginKind.FRAMEWORK);
        assertThat(descriptor.requires()).containsExactly("python");
        assertThat(descriptor.capabilities()).contains(
                PluginCapability.GRAPH, PluginCapability.INDEX, PluginCapability.VALIDATION);
        assertThat(descriptor.detection().alternatives()).hasSize(6);
    }
}
