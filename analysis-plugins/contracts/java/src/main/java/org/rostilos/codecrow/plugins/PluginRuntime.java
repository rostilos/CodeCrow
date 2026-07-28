package org.rostilos.codecrow.plugins;

import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.ServiceLoader;

/**
 * Host-owned composition of neutral plugin contributions.
 *
 * <p>Applications depend only on this contract. Concrete language, framework,
 * and domain
 * implementations remain discoverable runtime contributors.</p>
 */
public final class PluginRuntime {
    private final PluginRegistry registry;
    private final Map<String, CodeCrowPlugin> implementations;

    public PluginRuntime(Collection<? extends CodeCrowPlugin> plugins) {
        Map<String, CodeCrowPlugin> collected = new LinkedHashMap<>();
        for (CodeCrowPlugin plugin : plugins) {
            if (plugin == null) throw new IllegalArgumentException("plugin cannot be null");
            String pluginId = plugin.descriptor().id();
            if (collected.putIfAbsent(pluginId, plugin) != null) {
                throw new IllegalArgumentException("duplicate plugin implementation: " + pluginId);
            }
        }
        registry = PluginRegistry.fromPlugins(collected.values());
        implementations = Map.copyOf(collected);
    }

    public static PluginRuntime discover(ClassLoader classLoader) {
        List<CodeCrowPlugin> plugins = new ArrayList<>();
        ServiceLoader.load(CodeCrowPlugin.class, classLoader).forEach(plugins::add);
        return new PluginRuntime(plugins);
    }

    public PluginRegistry registry() {
        return registry;
    }

    /**
     * Compose selected file-policy contributions in deterministic registry
     * order. Excluded is strongest, followed by generated and
     * architecture-only. An explicit plugin failure aborts the host operation.
     */
    public FileDisposition fileDisposition(
            String path,
            ProjectCapabilities capabilities) {
        if (capabilities == null) {
            throw new IllegalArgumentException("project capabilities are required");
        }
        String normalizedPath = PluginValues.normalizePath(path);
        FileDisposition disposition = FileDisposition.FULL;
        for (String pluginId : capabilities.repositoryPlugins()) {
            PluginDescriptor descriptor = registry.descriptor(pluginId);
            if (!descriptor.capabilities().contains(PluginCapability.FILE_POLICY)) continue;
            CodeCrowPlugin implementation = implementations.get(pluginId);
            if (!(implementation instanceof FilePolicyPlugin contributor)) continue;

            PluginOutcome<FileDisposition> outcome;
            try {
                outcome = contributor.fileDisposition(normalizedPath);
            } catch (RuntimeException exception) {
                throw new IllegalStateException(
                        "plugin file policy threw for " + normalizedPath + ": " + pluginId,
                        exception);
            }
            if (outcome == null) {
                throw new IllegalStateException(
                        "plugin file policy returned null for " + normalizedPath + ": " + pluginId);
            }
            if (outcome.status() == OutcomeStatus.FAILED) {
                throw new IllegalStateException(
                        "plugin file policy failed for " + normalizedPath + ": "
                                + outcome.diagnostic().code());
            }
            if (outcome.status() != OutcomeStatus.HANDLED) continue;

            FileDisposition contribution = outcome.value();
            if (contribution == FileDisposition.EXCLUDED) return FileDisposition.EXCLUDED;
            if (contribution == FileDisposition.GENERATED) {
                disposition = FileDisposition.GENERATED;
            } else if (contribution == FileDisposition.ARCHITECTURE_ONLY
                    && disposition == FileDisposition.FULL) {
                disposition = FileDisposition.ARCHITECTURE_ONLY;
            }
        }
        return disposition;
    }
}
