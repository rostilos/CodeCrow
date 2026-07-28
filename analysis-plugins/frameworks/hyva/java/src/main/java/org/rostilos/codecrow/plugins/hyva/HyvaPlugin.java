package org.rostilos.codecrow.plugins.hyva;

import org.rostilos.codecrow.plugins.CodeCrowPlugin;
import org.rostilos.codecrow.plugins.PluginDescriptor;
import org.rostilos.codecrow.plugins.PluginManifestLoader;

public final class HyvaPlugin implements CodeCrowPlugin {
    private final PluginDescriptor descriptor;

    public HyvaPlugin() {
        try (var input = HyvaPlugin.class.getResourceAsStream(
                "/META-INF/codecrow/plugins/hyva/plugin.json")) {
            descriptor = new PluginManifestLoader().loadDescriptor(input);
        } catch (Exception exception) {
            throw new IllegalStateException("cannot load Hyva plugin descriptor", exception);
        }
    }

    @Override
    public PluginDescriptor descriptor() {
        return descriptor;
    }
}
