package org.rostilos.codecrow.plugins.ember;

import org.rostilos.codecrow.plugins.CodeCrowPlugin;
import org.rostilos.codecrow.plugins.PluginDescriptor;
import org.rostilos.codecrow.plugins.PluginManifestLoader;

public final class EmberPlugin implements CodeCrowPlugin {
    private final PluginDescriptor descriptor;

    public EmberPlugin() {
        try (var input = EmberPlugin.class.getResourceAsStream(
                "/META-INF/codecrow/plugins/ember/plugin.json")) {
            descriptor = new PluginManifestLoader().loadDescriptor(input);
        } catch (Exception exception) {
            throw new IllegalStateException("cannot load Ember plugin descriptor", exception);
        }
    }

    @Override
    public PluginDescriptor descriptor() {
        return descriptor;
    }
}
