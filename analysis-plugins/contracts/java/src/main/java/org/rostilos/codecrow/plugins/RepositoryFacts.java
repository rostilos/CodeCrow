package org.rostilos.codecrow.plugins;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

public record RepositoryFacts(
        String revision,
        List<String> paths,
        Map<String, String> markerContents,
        String projectType,
        String sourceRoot) {
    public RepositoryFacts(String revision, List<String> paths, Map<String, String> markerContents) {
        this(revision, paths, markerContents, null, null);
    }

    public RepositoryFacts {
        revision = PluginValues.requireNonBlank(revision, "revision");
        paths = PluginValues.sortedUnique(paths, "repository paths");
        for (String path : paths) {
            if (!path.equals(PluginValues.normalizePath(path))) {
                throw new IllegalArgumentException("repository paths must already be normalized");
            }
        }
        TreeMap<String, String> normalizedMarkers = new TreeMap<>();
        if (markerContents != null) {
            markerContents.forEach((path, content) -> {
                if (!path.equals(PluginValues.normalizePath(path))) {
                    throw new IllegalArgumentException("marker paths must already be normalized");
                }
                if (content == null) throw new IllegalArgumentException("marker content must be text");
                normalizedMarkers.put(path, content);
            });
        }
        markerContents = Collections.unmodifiableMap(new LinkedHashMap<>(normalizedMarkers));
        if (projectType != null && (projectType.isBlank() || "auto".equalsIgnoreCase(projectType.trim()))) {
            projectType = null;
        } else if (projectType != null) {
            projectType = PluginValues.requirePluginId(
                    projectType.trim().toLowerCase(java.util.Locale.ROOT), "project type");
        }
        if (sourceRoot != null && (sourceRoot.isBlank() || ".".equals(sourceRoot.trim()))) {
            sourceRoot = null;
        } else if (sourceRoot != null) {
            String normalized = PluginValues.normalizePath(sourceRoot.trim().replace('\\', '/'));
            if (!normalized.equals(sourceRoot.trim().replace('\\', '/'))) {
                throw new IllegalArgumentException("source root must already be normalized");
            }
            sourceRoot = normalized;
        }
    }
}
