package org.rostilos.codecrow.plugins.nextjs;

import org.rostilos.codecrow.plugins.CodeCrowPlugin;
import org.rostilos.codecrow.plugins.PluginDescriptor;
import org.rostilos.codecrow.plugins.PluginManifestLoader;

public final class NextJsPlugin implements CodeCrowPlugin {
    private final PluginDescriptor descriptor;

    public NextJsPlugin() {
        try (var input = NextJsPlugin.class.getResourceAsStream(
                "/META-INF/codecrow/plugins/nextjs/plugin.json")) {
            descriptor = new PluginManifestLoader().loadDescriptor(input);
        } catch (Exception exception) {
            throw new IllegalStateException("cannot load Next.js plugin descriptor", exception);
        }
    }

    @Override
    public PluginDescriptor descriptor() {
        return descriptor;
    }
}
