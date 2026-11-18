package org.rostilos.codecrow.plugins;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.Locale;
import java.util.Set;
import java.util.List;
import java.util.Map;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.HashSet;
import java.util.regex.Pattern;
import java.util.stream.Collectors;

/** Repository file scope shared by acquisition, request and persistence boundaries. */
public final class RepositoryFileScope {
    public static final Set<String> EXTENSIONS;
    static {
        try (var input = RepositoryFileScope.class.getResourceAsStream("/codecrow/excluded_file_extensions.txt")) {
            if (input == null) throw new IllegalStateException("Missing packaged file-scope extension catalog");
            EXTENSIONS = new BufferedReader(new InputStreamReader(input, StandardCharsets.UTF_8))
                    .lines().filter(line -> !line.isBlank() && !line.startsWith("#"))
                    .collect(Collectors.toUnmodifiableSet());
        } catch (java.io.IOException exception) {
            throw new ExceptionInInitializerError(exception);
        }
    }

    private RepositoryFileScope() {}

    public static boolean excludes(String path) {
        if (path == null) return false;
        String name = path.replace('\\', '/').toLowerCase(Locale.ROOT);
        name = name.substring(name.lastIndexOf('/') + 1);
        int dot = name.lastIndexOf('.');
        if (dot < 0) return false;
        if (Set.of("gz", "bz2", "xz", "zst", "br").contains(name.substring(dot + 1))) {
            name = name.substring(0, dot);
            dot = name.lastIndexOf('.');
        }
        return dot >= 0 && EXTENSIONS.contains(name.substring(dot + 1));
    }

    private static final Pattern DIFF_HEADER = Pattern.compile("^diff --git \"?a/(.*?)\"? \"?b/(.*?)\"?$");

    public static String filterDiff(String diff) {
        return filterDiff(diff, (oldPath, newPath) -> !excludes(oldPath) && !excludes(newPath));
    }

    public static String filterDiff(String diff, java.util.function.BiPredicate<String, String> includesChange) {
        if (diff == null) return null;
        StringBuilder result = new StringBuilder();
        for (String part : diff.split("(?m)(?=^diff --git )", -1)) {
            if (part.startsWith("diff --git ")) {
                String header = part.lines().findFirst().orElse("");
                var paths = DIFF_HEADER.matcher(header);
                if (paths.matches() && !includesChange.test(paths.group(1), paths.group(2))) continue;
            }
            result.append(part);
        }
        return result.toString();
    }

    private static final Set<String> PATH_FIELDS = Set.of("path", "filePath", "file_path", "file", "source", "target", "primary_file", "sourceFile", "targetFile", "filename");
    private static final Set<String> PATH_LISTS = Set.of("paths", "changedFiles", "deletedFiles", "relatedPaths", "affected_files",
            "queriedPaths", "unrepresentedPaths", "nonStructuralPaths", "reviewFilePaths", "fullPrFilePaths",
            "changed_files", "deleted_files", "targetFiles", "scanBatches");

    public static Object filterRecords(Object value) { return filterRecords(value, ""); }

    private static Object filterRecords(Object value, String field) {
        if (value instanceof Map<?, ?> map) {
            if (PATH_FIELDS.stream().anyMatch(key -> map.get(key) instanceof String path && excludes(path))) return null;
            if (Set.of("origin", "sourceUnit", "targetUnit").stream().anyMatch(key -> map.get(key) instanceof Map<?, ?> nested
                    && nested.get("path") instanceof String path && excludes(path))) return null;
            if (map.get("relatedPaths") instanceof List<?> paths
                    && paths.stream().anyMatch(path -> path instanceof String text && excludes(text))) return null;
            Set<Object> removedNodes = new HashSet<>();
            if (map.get("nodes") instanceof List<?> nodes) {
                for (Object node : nodes) {
                    if (node instanceof Map<?, ?> record && record.get("path") instanceof String path && excludes(path)
                            && (record.get("id") instanceof String || record.get("id") instanceof Number))
                        removedNodes.add(record.get("id"));
                }
            }
            Map<Object, Object> result = new LinkedHashMap<>();
            for (var entry : map.entrySet()) {
                String key = String.valueOf(entry.getKey());
                if (excludes(key)) continue;
                Object item = entry.getValue();
                if (key.equals("edges") && !removedNodes.isEmpty() && item instanceof List<?> edges) {
                    item = edges.stream().filter(edge -> !(edge instanceof Map<?, ?> record)
                            || (!removedNodes.contains(record.get("source")) && !removedNodes.contains(record.get("target")))).toList();
                }
                Object filtered = filterRecords(item, key);
                if (filtered != null || item == null) result.put(entry.getKey(), filtered);
            }
            if (map.get("paths") instanceof List<?> paths && !paths.isEmpty()
                    && result.get("paths") instanceof List<?> filtered && filtered.isEmpty()) return null;
            return result.equals(map) ? value : result;
        }
        if (value instanceof List<?> list) {
            List<Object> result = new ArrayList<>();
            for (Object item : list) {
                if (PATH_LISTS.contains(field) && item instanceof String path && excludes(path)) continue;
                Object filtered = filterRecords(item, field);
                if (filtered != null || item == null) result.add(filtered);
            }
            return result.equals(list) ? value : result;
        }
        if (value instanceof String text) {
            if (Set.of("rawDiff", "deltaDiff", "diff").contains(field)) return filterDiff(text);
            if (Set.of("oldPath", "old_path", "newPath").contains(field) && excludes(text)) return null;
        }
        return value;
    }
}
