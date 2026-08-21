package org.rostilos.codecrow.plugins.ember;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.plugins.PluginCapability;
import org.rostilos.codecrow.plugins.PluginKind;

import static org.assertj.core.api.Assertions.assertThat;

class EmberPluginTest {
    @Test
    void packagedManifestLoadsThroughTheJavaContract() {
        var descriptor = new EmberPlugin().descriptor();
        assertThat(descriptor.id()).isEqualTo("ember");
        assertThat(descriptor.kind()).isEqualTo(PluginKind.FRAMEWORK);
        assertThat(descriptor.requires()).containsExactly("json");
        assertThat(descriptor.capabilities()).contains(
                PluginCapability.GRAPH, PluginCapability.INDEX, PluginCapability.VALIDATION);
        assertThat(descriptor.detection().alternatives()).hasSize(2);
    }
}
