package org.rostilos.codecrow.plugins;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Objects;
import java.util.regex.Pattern;

final class PluginValues {
    private static final Pattern PLUGIN_ID = Pattern.compile("^[a-z][a-z0-9-]*$");
    private static final Pattern EXTENSION = Pattern.compile("^\\.[a-z0-9][a-z0-9.+_-]*$");
    private static final Pattern FINGERPRINT = Pattern.compile("^sha256:[0-9a-f]{64}$");

    private PluginValues() {}

    static String requirePluginId(String value, String field) {
        if (value == null || !PLUGIN_ID.matcher(value).matches()) {
            throw new IllegalArgumentException(field + " must match " + PLUGIN_ID.pattern());
        }
        return value;
    }

    static String requireNonBlank(String value, String field) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(field + " must be non-blank");
        }
        return value;
    }

    static String normalizePath(String value) {
        String path = requireNonBlank(value, "path").replace('\\', '/');
        while (path.startsWith("./")) path = path.substring(2);
        if (path.startsWith("/") || path.endsWith("/") || path.contains("//")) {
            throw new IllegalArgumentException("path must be a normalized repository-relative path");
        }
        for (String segment : path.split("/", -1)) {
            if (segment.isEmpty() || ".".equals(segment) || "..".equals(segment)) {
                throw new IllegalArgumentException("path contains an invalid segment");
            }
        }
        return path;
    }

    static boolean isExtension(String value) {
        return value != null && EXTENSION.matcher(value).matches();
    }

    static String requireFingerprint(String value) {
        if (value == null || !FINGERPRINT.matcher(value).matches()) {
            throw new IllegalArgumentException(
                    "fingerprint must be a lowercase SHA-256 content digest");
        }
        return value;
    }

    static <T extends Comparable<? super T>> List<T> sortedUnique(List<T> input, String field) {
        List<T> values = input == null ? List.of() : List.copyOf(input);
        if (values.stream().anyMatch(Objects::isNull)) {
            throw new IllegalArgumentException(field + " cannot contain null values");
        }
        List<T> sorted = new ArrayList<>(values);
        sorted.sort(null);
        if (!values.equals(sorted) || new HashSet<>(values).size() != values.size()) {
            throw new IllegalArgumentException(field + " must be unique and sorted");
        }
        return values;
    }

    static <T> List<T> unique(List<T> input, String field) {
        List<T> values = input == null ? List.of() : List.copyOf(input);
        if (values.stream().anyMatch(Objects::isNull)) {
            throw new IllegalArgumentException(field + " cannot contain null values");
        }
        if (new HashSet<>(values).size() != values.size()) {
            throw new IllegalArgumentException(field + " must be unique");
        }
        return values;
    }
}
