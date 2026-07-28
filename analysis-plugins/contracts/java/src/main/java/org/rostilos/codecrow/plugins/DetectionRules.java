package org.rostilos.codecrow.plugins;

import java.util.List;

public record DetectionRules(
        List<String> extensions,
        List<String> filesAll,
        List<String> filesAny,
        List<ContentMarker> contentMarkers,
        List<DetectionAlternative> alternatives) {

    public DetectionRules {
        extensions = PluginValues.sortedUnique(extensions, "detection extensions");
        if (extensions.stream().anyMatch(value -> !PluginValues.isExtension(value))) {
            throw new IllegalArgumentException("detection extensions must be normalized lowercase extensions");
        }
        filesAll = normalizedPaths(filesAll, "filesAll");
        filesAny = normalizedPaths(filesAny, "filesAny");
        contentMarkers = PluginValues.sortedUnique(contentMarkers, "contentMarkers");
        alternatives = PluginValues.sortedUnique(alternatives, "detection alternatives");
    }

    public static DetectionRules empty() {
        return new DetectionRules(List.of(), List.of(), List.of(), List.of(), List.of());
    }

    private static List<String> normalizedPaths(List<String> input, String field) {
        List<String> paths = PluginValues.sortedUnique(input, field);
        for (String path : paths) {
            if (!path.equals(PluginValues.normalizePath(path))) {
                throw new IllegalArgumentException(field + " paths must already be normalized");
            }
        }
        return paths;
    }
}
