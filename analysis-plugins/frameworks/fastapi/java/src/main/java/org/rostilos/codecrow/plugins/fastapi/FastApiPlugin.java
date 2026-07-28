package org.rostilos.codecrow.plugins.fastapi;

import org.rostilos.codecrow.plugins.CodeCrowPlugin;
import org.rostilos.codecrow.plugins.PluginDescriptor;
import org.rostilos.codecrow.plugins.PluginManifestLoader;

public final class FastApiPlugin implements CodeCrowPlugin {
    private final PluginDescriptor descriptor;

    public FastApiPlugin() {
        try (var input = FastApiPlugin.class.getResourceAsStream(
                "/META-INF/codecrow/plugins/fastapi/plugin.json")) {
            descriptor = new PluginManifestLoader().loadDescriptor(input);
        } catch (Exception exception) {
            throw new IllegalStateException("cannot load FastAPI plugin descriptor", exception);
        }
    }

    @Override
    public PluginDescriptor descriptor() {
        return descriptor;
    }
}
