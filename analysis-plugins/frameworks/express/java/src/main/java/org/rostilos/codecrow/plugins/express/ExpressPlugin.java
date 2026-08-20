package org.rostilos.codecrow.plugins.express;

import org.rostilos.codecrow.plugins.CodeCrowPlugin;
import org.rostilos.codecrow.plugins.PluginDescriptor;
import org.rostilos.codecrow.plugins.PluginManifestLoader;

public final class ExpressPlugin implements CodeCrowPlugin {
    private final PluginDescriptor descriptor;

    public ExpressPlugin() {
        try (var input = ExpressPlugin.class.getResourceAsStream(
                "/META-INF/codecrow/plugins/express/plugin.json")) {
            descriptor = new PluginManifestLoader().loadDescriptor(input);
        } catch (Exception exception) {
            throw new IllegalStateException("cannot load Express plugin descriptor", exception);
        }
    }

    @Override
    public PluginDescriptor descriptor() {
        return descriptor;
    }
}
