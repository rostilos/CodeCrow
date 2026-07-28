package org.rostilos.codecrow.plugins.spring;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.plugins.PluginCapability;
import org.rostilos.codecrow.plugins.PluginKind;

import static org.assertj.core.api.Assertions.assertThat;

class SpringPluginTest {
    @Test
    void packagedManifestLoadsThroughTheJavaContract() {
        var descriptor = new SpringPlugin().descriptor();
        assertThat(descriptor.id()).isEqualTo("spring");
        assertThat(descriptor.kind()).isEqualTo(PluginKind.FRAMEWORK);
        assertThat(descriptor.requires()).containsExactly("java");
        assertThat(descriptor.capabilities()).contains(
                PluginCapability.GRAPH, PluginCapability.INDEX, PluginCapability.VALIDATION);
        assertThat(descriptor.detection().alternatives()).hasSize(7);
    }
}
