package org.rostilos.codecrow.core.model.project.config;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonIgnore;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.Locale;
import java.util.regex.Pattern;

/**
 * Authoritative project-level plugin selection and optional source boundary.
 * A null project type means automatic, marker-based detection.
 */
public record AnalysisProfileConfig(String projectType, String sourceRoot) {
    private static final Pattern PLUGIN_ID = Pattern.compile("[a-z][a-z0-9-]{0,63}");

    @JsonCreator
    public AnalysisProfileConfig(
            @JsonProperty("projectType") String projectType,
            @JsonProperty("sourceRoot") String sourceRoot) {
        this.projectType = normalizeProjectType(projectType);
        this.sourceRoot = normalizeSourceRoot(sourceRoot);
    }

    @JsonIgnore
    public boolean isAutomatic() {
        return projectType == null;
    }

    private static String normalizeProjectType(String value) {
        if (value == null || value.isBlank() || "auto".equalsIgnoreCase(value.trim())) {
            return null;
        }
        String normalized = value.trim().toLowerCase(Locale.ROOT);
        if (!PLUGIN_ID.matcher(normalized).matches()) {
            throw new IllegalArgumentException("projectType must be a valid plugin id");
        }
        return normalized;
    }

    private static String normalizeSourceRoot(String value) {
        if (value == null || value.isBlank() || ".".equals(value.trim())) {
            return null;
        }
        String normalized = value.trim().replace('\\', '/');
        if (normalized.startsWith("/") || normalized.endsWith("/")) {
            throw new IllegalArgumentException("sourceRoot must be repository-relative without a trailing slash");
        }
        for (String segment : normalized.split("/", -1)) {
            if (segment.isBlank() || ".".equals(segment) || "..".equals(segment)) {
                throw new IllegalArgumentException("sourceRoot contains an invalid path segment");
            }
        }
        return normalized;
    }
}
