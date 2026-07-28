package org.rostilos.codecrow.plugins.hyva;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.plugins.PluginCapability;

import static org.assertj.core.api.Assertions.assertThat;

class HyvaPluginTest {
    @Test
    void exposesFrameworkDescriptorWithoutHostSpecificBehavior() {
        var descriptor = new HyvaPlugin().descriptor();

        assertThat(descriptor.id()).isEqualTo("hyva");
        assertThat(descriptor.requires()).containsExactly("magento");
        assertThat(descriptor.capabilities()).containsExactly(
                PluginCapability.CONTEXT,
                PluginCapability.GRAPH,
                PluginCapability.PLANNING,
                PluginCapability.VALIDATION);
    }
}
