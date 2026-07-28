package org.rostilos.codecrow.plugins.datacontracts;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.plugins.PluginKind;
import org.rostilos.codecrow.plugins.PluginRuntime;

import static org.assertj.core.api.Assertions.assertThat;

class DataContractsPluginTest {
    @Test
    void exposesDomainDescriptorWithoutApplicationReleaseMetadata() {
        var descriptor = new DataContractsPlugin().descriptor();

        assertThat(descriptor.id()).isEqualTo("data-contracts");
        assertThat(descriptor.kind()).isEqualTo(PluginKind.DOMAIN);
        assertThat(descriptor.requires()).isEmpty();
    }

    @Test
    void isDiscoveredThroughTheNeutralServiceLoaderContract() {
        var runtime = PluginRuntime.discover(
                DataContractsPlugin.class.getClassLoader());

        assertThat(runtime.registry().orderedIds()).contains("data-contracts");
    }
}
