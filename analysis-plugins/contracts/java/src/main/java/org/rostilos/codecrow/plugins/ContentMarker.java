package org.rostilos.codecrow.plugins;

public record ContentMarker(String path, String contains) implements Comparable<ContentMarker> {
    public ContentMarker {
        path = PluginValues.normalizePath(path);
        contains = PluginValues.requireNonBlank(contains, "content marker");
    }

    @Override
    public int compareTo(ContentMarker other) {
        int pathComparison = path.compareTo(other.path);
        return pathComparison != 0 ? pathComparison : contains.compareTo(other.contains);
    }
}
