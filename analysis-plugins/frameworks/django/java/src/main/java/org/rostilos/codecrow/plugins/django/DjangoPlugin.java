package org.rostilos.codecrow.plugins.django;

import org.rostilos.codecrow.plugins.CodeCrowPlugin;
import org.rostilos.codecrow.plugins.PluginDescriptor;
import org.rostilos.codecrow.plugins.PluginManifestLoader;

public final class DjangoPlugin implements CodeCrowPlugin {
    private final PluginDescriptor descriptor;

    public DjangoPlugin() {
        try (var input = DjangoPlugin.class.getResourceAsStream(
                "/META-INF/codecrow/plugins/django/plugin.json")) {
            descriptor = new PluginManifestLoader().loadDescriptor(input);
        } catch (Exception exception) {
            throw new IllegalStateException("cannot load Django plugin descriptor", exception);
        }
    }

    @Override
    public PluginDescriptor descriptor() {
        return descriptor;
    }
}
