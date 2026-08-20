package org.rostilos.codecrow.plugins.express;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.plugins.PluginCapability;
import org.rostilos.codecrow.plugins.PluginKind;

import static org.assertj.core.api.Assertions.assertThat;

class ExpressPluginTest {
    @Test
    void packagedManifestLoadsThroughTheJavaContract() {
        var descriptor = new ExpressPlugin().descriptor();
        assertThat(descriptor.id()).isEqualTo("express");
        assertThat(descriptor.kind()).isEqualTo(PluginKind.FRAMEWORK);
        assertThat(descriptor.requires()).containsExactly("json");
        assertThat(descriptor.capabilities()).contains(
                PluginCapability.GRAPH, PluginCapability.INDEX, PluginCapability.VALIDATION);
        assertThat(descriptor.detection().alternatives()).hasSize(1);
    }
}
