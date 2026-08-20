package org.rostilos.codecrow.plugins.quarkus;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.plugins.PluginCapability;
import org.rostilos.codecrow.plugins.PluginKind;

import static org.assertj.core.api.Assertions.assertThat;

class QuarkusPluginTest {
    @Test
    void packagedManifestLoadsThroughTheJavaContract() {
        var descriptor = new QuarkusPlugin().descriptor();

        assertThat(descriptor.id()).isEqualTo("quarkus");
        assertThat(descriptor.kind()).isEqualTo(PluginKind.FRAMEWORK);
        assertThat(descriptor.requires()).containsExactly("java");
        assertThat(descriptor.capabilities()).contains(
                PluginCapability.GRAPH,
                PluginCapability.INDEX,
                PluginCapability.VALIDATION);
        assertThat(descriptor.detection().alternatives()).hasSize(4);
    }
}
