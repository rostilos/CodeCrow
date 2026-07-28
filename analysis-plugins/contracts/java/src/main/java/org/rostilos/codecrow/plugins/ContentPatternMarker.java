package org.rostilos.codecrow.plugins;

public record ContentPatternMarker(String pathPattern, String contains)
        implements Comparable<ContentPatternMarker> {
    public ContentPatternMarker {
        pathPattern = DetectionAlternative.normalizePattern(pathPattern, "content marker pathPattern");
        contains = PluginValues.requireNonBlank(contains, "content pattern marker");
    }

    @Override
    public int compareTo(ContentPatternMarker other) {
        int pathComparison = pathPattern.compareTo(other.pathPattern);
        return pathComparison != 0 ? pathComparison : contains.compareTo(other.contains);
    }
}
