package org.rostilos.codecrow.plugins.datacontracts;

import org.rostilos.codecrow.plugins.CodeCrowPlugin;
import org.rostilos.codecrow.plugins.PluginDescriptor;
import org.rostilos.codecrow.plugins.PluginManifestLoader;

public final class DataContractsPlugin implements CodeCrowPlugin {
    private final PluginDescriptor descriptor;

    public DataContractsPlugin() {
        try (var input = DataContractsPlugin.class.getResourceAsStream(
                "/META-INF/codecrow/plugins/data-contracts/plugin.json")) {
            descriptor = new PluginManifestLoader().loadDescriptor(input);
        } catch (Exception exception) {
            throw new IllegalStateException(
                    "cannot load data-contracts plugin descriptor",
                    exception);
        }
    }

    @Override
    public PluginDescriptor descriptor() {
        return descriptor;
    }
}
