package org.rostilos.codecrow.plugins.rails;

import org.rostilos.codecrow.plugins.CodeCrowPlugin;
import org.rostilos.codecrow.plugins.PluginDescriptor;
import org.rostilos.codecrow.plugins.PluginManifestLoader;

public final class RailsPlugin implements CodeCrowPlugin {
    private final PluginDescriptor descriptor;

    public RailsPlugin() {
        try (var input = RailsPlugin.class.getResourceAsStream(
                "/META-INF/codecrow/plugins/rails/plugin.json")) {
            descriptor = new PluginManifestLoader().loadDescriptor(input);
        } catch (Exception exception) {
            throw new IllegalStateException("cannot load Rails plugin descriptor", exception);
        }
    }

    @Override
    public PluginDescriptor descriptor() {
        return descriptor;
    }
}
