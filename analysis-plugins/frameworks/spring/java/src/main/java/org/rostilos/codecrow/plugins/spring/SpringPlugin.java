package org.rostilos.codecrow.plugins.spring;

import org.rostilos.codecrow.plugins.CodeCrowPlugin;
import org.rostilos.codecrow.plugins.PluginDescriptor;
import org.rostilos.codecrow.plugins.PluginManifestLoader;

public final class SpringPlugin implements CodeCrowPlugin {
    private final PluginDescriptor descriptor;

    public SpringPlugin() {
        try (var input = SpringPlugin.class.getResourceAsStream(
                "/META-INF/codecrow/plugins/spring/plugin.json")) {
            descriptor = new PluginManifestLoader().loadDescriptor(input);
        } catch (Exception exception) {
            throw new IllegalStateException("cannot load Spring plugin descriptor", exception);
        }
    }

    @Override
    public PluginDescriptor descriptor() {
        return descriptor;
    }
}
