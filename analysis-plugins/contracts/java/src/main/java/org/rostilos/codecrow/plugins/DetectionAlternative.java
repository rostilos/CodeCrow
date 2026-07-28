package org.rostilos.codecrow.plugins;

import java.util.List;

public record DetectionAlternative(
        List<String> filesAll,
        List<String> filesAny,
        List<String> pathPatternsAll,
        List<String> pathPatternsAny,
        List<ContentMarker> contentMarkers,
        List<ContentPatternMarker> contentPatternMarkers) implements Comparable<DetectionAlternative> {

    public DetectionAlternative(
            List<String> filesAll,
            List<String> filesAny,
            List<String> pathPatternsAll,
            List<String> pathPatternsAny,
            List<ContentMarker> contentMarkers) {
        this(filesAll, filesAny, pathPatternsAll, pathPatternsAny, contentMarkers, List.of());
    }

    public DetectionAlternative {
        filesAll = normalizedPaths(filesAll, "filesAll");
        filesAny = normalizedPaths(filesAny, "filesAny");
        pathPatternsAll = normalizedPatterns(pathPatternsAll, "pathPatternsAll");
        pathPatternsAny = normalizedPatterns(pathPatternsAny, "pathPatternsAny");
        contentMarkers = PluginValues.sortedUnique(contentMarkers, "contentMarkers");
        contentPatternMarkers = PluginValues.sortedUnique(contentPatternMarkers, "contentPatternMarkers");
        if (filesAll.isEmpty() && filesAny.isEmpty()
                && pathPatternsAll.isEmpty() && pathPatternsAny.isEmpty()
                && contentMarkers.isEmpty() && contentPatternMarkers.isEmpty()) {
            throw new IllegalArgumentException("detection alternative must contain at least one condition");
        }
    }

    @Override
    public int compareTo(DetectionAlternative other) {
        int result = compareLists(filesAll, other.filesAll);
        if (result != 0) return result;
        result = compareLists(filesAny, other.filesAny);
        if (result != 0) return result;
        result = compareLists(pathPatternsAll, other.pathPatternsAll);
        if (result != 0) return result;
        result = compareLists(pathPatternsAny, other.pathPatternsAny);
        if (result != 0) return result;
        result = compareLists(contentMarkers, other.contentMarkers);
        if (result != 0) return result;
        return compareLists(contentPatternMarkers, other.contentPatternMarkers);
    }

    private static <T extends Comparable<? super T>> int compareLists(
            List<T> left,
            List<T> right) {
        int commonLength = Math.min(left.size(), right.size());
        for (int index = 0; index < commonLength; index++) {
            int result = left.get(index).compareTo(right.get(index));
            if (result != 0) return result;
        }
        return Integer.compare(left.size(), right.size());
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

    private static List<String> normalizedPatterns(List<String> input, String field) {
        List<String> patterns = PluginValues.sortedUnique(input, field);
        for (String pattern : patterns) {
            if (pattern.isBlank() || pattern.startsWith("/") || pattern.contains("\\")
                    || pattern.contains("//") || pattern.contains("/../")
                    || pattern.startsWith("../") || pattern.endsWith("/..")) {
                throw new IllegalArgumentException(field + " must contain normalized repository-relative globs");
            }
        }
        return patterns;
    }

    static String normalizePattern(String pattern, String field) {
        return normalizedPatterns(List.of(pattern), field).get(0);
    }
}
