package org.rostilos.codecrow.plugins;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

public record PluginDescriptor(
        String id,
        PluginKind kind,
        List<String> requires,
        List<PluginCapability> capabilities,
        DetectionRules detection,
        Map<String, String> entrypoints) {

    public PluginDescriptor {
        id = PluginValues.requirePluginId(id, "plugin id");
        if (kind == null) throw new IllegalArgumentException("kind is required");
        requires = PluginValues.sortedUnique(requires, "requires");
        for (String requirement : requires) {
            PluginValues.requirePluginId(requirement, "required plugin id");
        }
        if (requires.contains(id)) throw new IllegalArgumentException("a plugin cannot require itself");
        capabilities = capabilities == null ? List.of() : List.copyOf(capabilities);
        List<String> capabilityValues = capabilities.stream().map(PluginCapability::value).toList();
        PluginValues.sortedUnique(capabilityValues, "capabilities");
        if (detection == null) detection = DetectionRules.empty();
        TreeMap<String, String> normalizedEntrypoints = new TreeMap<>();
        if (entrypoints != null) {
            entrypoints.forEach((runtime, entrypoint) -> {
                if (!"java".equals(runtime) && !"python".equals(runtime)) {
                    throw new IllegalArgumentException("unsupported runtime entrypoint: " + runtime);
                }
                normalizedEntrypoints.put(runtime,
                        PluginValues.requireNonBlank(entrypoint, "entrypoint"));
            });
        }
        entrypoints = Collections.unmodifiableMap(new LinkedHashMap<>(normalizedEntrypoints));
    }
}
