package org.rostilos.codecrow.core.model.project.config;

import java.nio.file.FileSystems;
import java.nio.file.Path;
import java.util.LinkedHashSet;
import java.util.List;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

/** File-path scope used by PR and branch analysis. Exclusions win over inclusions. */
@JsonIgnoreProperties(ignoreUnknown = true)
public record AnalysisScopeConfig(
        @JsonProperty("includePatterns") List<String> includePatterns,
        @JsonProperty("excludePatterns") List<String> excludePatterns) {

    public AnalysisScopeConfig {
        includePatterns = normalize(includePatterns);
        excludePatterns = normalize(excludePatterns);
    }

    public AnalysisScopeConfig() {
        this(List.of(), List.of());
    }

    private static List<String> normalize(List<String> patterns) {
        if (patterns == null) {
            return List.of();
        }
        LinkedHashSet<String> normalized = new LinkedHashSet<>();
        for (String pattern : patterns) {
            if (pattern != null && !pattern.isBlank()) {
                normalized.add(pattern.trim().replace('\\', '/'));
            }
        }
        return List.copyOf(normalized);
    }

    public boolean includes(String repositoryPath) {
        if (repositoryPath == null || repositoryPath.isBlank()) return false;
        String path = normalizePath(repositoryPath);
        boolean included = includePatterns.isEmpty()
                || includePatterns.stream().anyMatch(pattern -> matches(path, pattern));
        return included && excludePatterns.stream().noneMatch(pattern -> matches(path, pattern));
    }

    public boolean includesChange(String oldPath, String newPath) {
        // Destination paths define additions, modifications and renames. A deletion
        // has no destination, so its former path determines whether it is in scope.
        return newPath != null ? includes(newPath) : includes(oldPath);
    }

    private static boolean matches(String path, String configuredPattern) {
        String pattern = normalizePath(configuredPattern);
        if (pattern.isBlank()) return false;
        if (pattern.endsWith("/")) {
            String directory = pattern.substring(0, pattern.length() - 1);
            return path.equals(directory) || path.startsWith(directory + "/");
        }

        try {
            Path candidate = Path.of(path);
            String effectivePattern = pattern.contains("/") ? pattern : "**/" + pattern;
            return globMatches(candidate, effectivePattern);
        } catch (IllegalArgumentException e) {
            return false;
        }
    }

    private static boolean globMatches(Path path, String pattern) {
        if (FileSystems.getDefault().getPathMatcher("glob:" + pattern).matches(path)) {
            return true;
        }

        // JDK glob **/ requires at least one directory. RAG follows standard
        // globstar semantics where it may also represent zero directories.
        int globstar = pattern.indexOf("**/");
        while (globstar >= 0) {
            String zeroDirectoryVariant = pattern.substring(0, globstar)
                    + pattern.substring(globstar + 3);
            if (globMatches(path, zeroDirectoryVariant)) return true;
            globstar = pattern.indexOf("**/", globstar + 3);
        }
        return false;
    }

    private static String normalizePath(String value) {
        String normalized = value.trim().replace('\\', '/');
        while (normalized.startsWith("./")) normalized = normalized.substring(2);
        while (normalized.startsWith("/")) normalized = normalized.substring(1);
        return normalized;
    }
}
