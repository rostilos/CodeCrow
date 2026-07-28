package org.rostilos.codecrow.plugins;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

public record ProjectCapabilities(
        List<String> repositoryPlugins,
        Map<String, List<String>> filePlugins,
        Map<String, List<String>> detectionEvidence,
        List<String> unavailableCapabilities,
        String fingerprint,
        String descriptorFingerprint) {

    public ProjectCapabilities(
            List<String> repositoryPlugins,
            Map<String, List<String>> filePlugins,
            Map<String, List<String>> detectionEvidence,
            List<String> unavailableCapabilities,
            String fingerprint) {
        this(
                repositoryPlugins,
                filePlugins,
                detectionEvidence,
                unavailableCapabilities,
                fingerprint,
                "sha256:" + "0".repeat(64));
    }

    public ProjectCapabilities {
        repositoryPlugins = PluginValues.unique(repositoryPlugins, "repository plugins");
        repositoryPlugins.forEach(id -> PluginValues.requirePluginId(id, "plugin id"));
        filePlugins = normalizedFilePlugins(filePlugins);
        detectionEvidence = normalizedEvidence(detectionEvidence);
        unavailableCapabilities = PluginValues.sortedUnique(
                unavailableCapabilities, "unavailable capabilities");
        fingerprint = PluginValues.requireFingerprint(fingerprint);
        descriptorFingerprint = PluginValues.requireFingerprint(descriptorFingerprint);
    }

    private static Map<String, List<String>> normalizedFilePlugins(Map<String, List<String>> input) {
        TreeMap<String, List<String>> result = new TreeMap<>();
        if (input != null) {
            input.forEach((path, pluginIds) -> {
                if (!path.equals(PluginValues.normalizePath(path))) {
                    throw new IllegalArgumentException("file plugin paths must already be normalized");
                }
                List<String> ids = PluginValues.unique(pluginIds, "file plugins for " + path);
                ids.forEach(id -> PluginValues.requirePluginId(id, "plugin id"));
                result.put(path, ids);
            });
        }
        return Collections.unmodifiableMap(new LinkedHashMap<>(result));
    }

    private static Map<String, List<String>> normalizedEvidence(Map<String, List<String>> input) {
        TreeMap<String, List<String>> result = new TreeMap<>();
        if (input != null) {
            input.forEach((pluginId, evidence) -> {
                PluginValues.requirePluginId(pluginId, "plugin id");
                result.put(pluginId, PluginValues.sortedUnique(evidence,
                        "detection evidence for " + pluginId));
            });
        }
        return Collections.unmodifiableMap(new LinkedHashMap<>(result));
    }
}
