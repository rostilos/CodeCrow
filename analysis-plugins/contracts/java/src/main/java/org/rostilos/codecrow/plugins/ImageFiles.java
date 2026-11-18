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

/** Unconditional repository image exclusion shared with the Python hosts. */
public final class ImageFiles {
    public static final Set<String> EXTENSIONS;
    static {
        try (var input = ImageFiles.class.getResourceAsStream("/codecrow/image_extensions.txt")) {
            if (input == null) throw new IllegalStateException("Missing packaged image extension catalog");
            EXTENSIONS = new BufferedReader(new InputStreamReader(input, StandardCharsets.UTF_8))
                    .lines().filter(line -> !line.isBlank() && !line.startsWith("#"))
                    .collect(Collectors.toUnmodifiableSet());
        } catch (java.io.IOException exception) {
            throw new ExceptionInInitializerError(exception);
        }
    }

    private ImageFiles() {}

    public static boolean isImage(String path) {
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

    public static String filterDiff(String diff) {
        if (diff == null) return null;
        StringBuilder result = new StringBuilder();
        for (String part : diff.split("(?m)(?=^diff --git )", -1)) {
            if (part.startsWith("diff --git ")) {
                String header = part.lines().findFirst().orElse("");
                if (Pattern.compile("\\s+").splitAsStream(header)
                        .map(token -> token.replace("\"", "")).anyMatch(ImageFiles::isImage)) continue;
            }
            result.append(part);
        }
        return result.toString();
    }

    private static final Set<String> PATH_FIELDS = Set.of("path", "filePath", "file_path", "file", "source", "target", "primary_file");
    private static final Set<String> PATH_LISTS = Set.of("paths", "changedFiles", "deletedFiles", "relatedPaths", "affected_files",
            "queriedPaths", "unrepresentedPaths", "nonStructuralPaths", "reviewFilePaths", "fullPrFilePaths",
            "changed_files", "deleted_files", "targetFiles", "scanBatches");

    public static Object omitRecords(Object value) { return omitRecords(value, ""); }

    private static Object omitRecords(Object value, String field) {
        if (value instanceof Map<?, ?> map) {
            if (PATH_FIELDS.stream().anyMatch(key -> map.get(key) instanceof String path && isImage(path))) return null;
            Set<Object> removedNodes = new HashSet<>();
            if (map.get("nodes") instanceof List<?> nodes) {
                for (Object node : nodes) {
                    if (node instanceof Map<?, ?> record && record.get("path") instanceof String path && isImage(path))
                        removedNodes.add(record.get("id"));
                }
            }
            Map<Object, Object> result = new LinkedHashMap<>();
            for (var entry : map.entrySet()) {
                String key = String.valueOf(entry.getKey());
                if (isImage(key)) continue;
                Object item = entry.getValue();
                if (key.equals("edges") && !removedNodes.isEmpty() && item instanceof List<?> edges) {
                    item = edges.stream().filter(edge -> !(edge instanceof Map<?, ?> record)
                            || (!removedNodes.contains(record.get("source")) && !removedNodes.contains(record.get("target")))).toList();
                }
                Object filtered = omitRecords(item, key);
                if (filtered != null || item == null) result.put(entry.getKey(), filtered);
            }
            if (map.get("paths") instanceof List<?> paths && !paths.isEmpty()
                    && result.get("paths") instanceof List<?> filtered && filtered.isEmpty()) return null;
            return result.equals(map) ? value : result;
        }
        if (value instanceof List<?> list) {
            List<Object> result = new ArrayList<>();
            for (Object item : list) {
                if (PATH_LISTS.contains(field) && item instanceof String path && isImage(path)) continue;
                Object filtered = omitRecords(item, field);
                if (filtered != null || item == null) result.add(filtered);
            }
            return result.equals(list) ? value : result;
        }
        if (value instanceof String text) {
            if (Set.of("rawDiff", "deltaDiff", "diff").contains(field)) return filterDiff(text);
            if (Set.of("oldPath", "old_path", "newPath").contains(field) && isImage(text)) return null;
        }
        return value;
    }
}
