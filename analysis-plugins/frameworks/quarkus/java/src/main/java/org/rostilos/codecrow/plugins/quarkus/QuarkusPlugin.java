package org.rostilos.codecrow.plugins.quarkus;

import org.rostilos.codecrow.plugins.CodeCrowPlugin;
import org.rostilos.codecrow.plugins.PluginDescriptor;
import org.rostilos.codecrow.plugins.PluginManifestLoader;

public final class QuarkusPlugin implements CodeCrowPlugin {
    private final PluginDescriptor descriptor;

    public QuarkusPlugin() {
        try (var input = QuarkusPlugin.class.getResourceAsStream(
                "/META-INF/codecrow/plugins/quarkus/plugin.json")) {
            descriptor = new PluginManifestLoader().loadDescriptor(input);
        } catch (Exception exception) {
            throw new IllegalStateException(
                    "cannot load Quarkus plugin descriptor",
                    exception);
        }
    }

    @Override
    public PluginDescriptor descriptor() {
        return descriptor;
    }
}
