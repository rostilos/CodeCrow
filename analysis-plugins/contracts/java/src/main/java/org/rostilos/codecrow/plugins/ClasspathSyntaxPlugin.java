package org.rostilos.codecrow.plugins;

import java.util.List;

/**
 * Base implementation for a language plugin packaged as a self-contained JAR.
 */
public abstract class ClasspathSyntaxPlugin implements SyntaxPlugin {
    private final PluginDescriptor descriptor;
    private final SyntaxContribution contribution;

    protected ClasspathSyntaxPlugin(
            Class<?> owner,
            String pluginId,
            List<String> extensions,
            String scopeQueryResource,
            SyntaxGrammarFactory grammarFactory) {
        String descriptorResource = "/META-INF/codecrow/plugins/" + pluginId + "/plugin.json";
        try (var input = owner.getResourceAsStream(descriptorResource)) {
            descriptor = new PluginManifestLoader().loadDescriptor(input);
        } catch (Exception exception) {
            throw new IllegalStateException("cannot load " + pluginId + " plugin descriptor", exception);
        }
        if (!descriptor.id().equals(pluginId)) {
            throw new IllegalStateException("plugin descriptor ID does not match " + pluginId);
        }
        contribution = new SyntaxContribution(
                pluginId, extensions, scopeQueryResource, grammarFactory);
    }

    @Override
    public final PluginDescriptor descriptor() {
        return descriptor;
    }

    @Override
    public final PluginOutcome<SyntaxContribution> syntaxContribution() {
        return PluginOutcome.handled(contribution);
    }
}
