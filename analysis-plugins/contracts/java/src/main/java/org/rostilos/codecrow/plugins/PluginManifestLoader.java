package org.rostilos.codecrow.plugins;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

public final class PluginManifestLoader {
    private static final Set<String> DESCRIPTOR_FIELDS = Set.of(
            "id", "kind", "requires", "capabilities", "detection", "entrypoints");
    private static final Set<String> DETECTION_FIELDS = Set.of(
            "extensions", "filesAll", "filesAny", "contentMarkers", "alternatives");
    private static final Set<String> ALTERNATIVE_FIELDS = Set.of(
            "filesAll", "filesAny", "pathPatternsAll", "pathPatternsAny", "contentMarkers");
    private static final Set<String> ALTERNATIVE_OPTIONAL_FIELDS = Set.of("contentPatternMarkers");
    private static final Set<String> MARKER_FIELDS = Set.of("path", "contains");
    private static final Set<String> PATTERN_MARKER_FIELDS = Set.of("pathPattern", "contains");

    private final ObjectMapper mapper;

    public PluginManifestLoader() {
        this(new ObjectMapper());
    }

    PluginManifestLoader(ObjectMapper mapper) {
        this.mapper = mapper;
    }

    public PluginDescriptor loadDescriptor(Path path) throws IOException {
        return parseDescriptor(mapper.readTree(path.toFile()));
    }

    public PluginDescriptor loadDescriptor(InputStream input) throws IOException {
        if (input == null) throw new IllegalArgumentException("plugin descriptor input is required");
        return parseDescriptor(mapper.readTree(input));
    }

    public List<PluginDescriptor> loadDescriptors(Path path) throws IOException {
        JsonNode root = mapper.readTree(path.toFile());
        if (!root.isArray()) throw new IllegalArgumentException("plugin descriptor collection must be an array");
        List<PluginDescriptor> descriptors = new ArrayList<>();
        for (JsonNode node : root) descriptors.add(parseDescriptor(node));
        return List.copyOf(descriptors);
    }

    PluginDescriptor parseDescriptor(JsonNode node) {
        requireObject(node, "plugin descriptor");
        requireExactFields(node, DESCRIPTOR_FIELDS, "plugin descriptor");
        JsonNode detectionNode = node.get("detection");
        requireObject(detectionNode, "detection");
        requireExactFields(detectionNode, DETECTION_FIELDS, "detection");

        List<ContentMarker> markers = markers(detectionNode.get("contentMarkers"), "contentMarkers");
        List<DetectionAlternative> alternatives = new ArrayList<>();
        JsonNode alternativesNode = requireArray(detectionNode.get("alternatives"), "alternatives");
        for (JsonNode alternativeNode : alternativesNode) {
            requireObject(alternativeNode, "detection alternative");
            requireFields(alternativeNode, ALTERNATIVE_FIELDS, ALTERNATIVE_OPTIONAL_FIELDS, "detection alternative");
            alternatives.add(new DetectionAlternative(
                    textArray(alternativeNode.get("filesAll"), "filesAll"),
                    textArray(alternativeNode.get("filesAny"), "filesAny"),
                    textArray(alternativeNode.get("pathPatternsAll"), "pathPatternsAll"),
                    textArray(alternativeNode.get("pathPatternsAny"), "pathPatternsAny"),
                    markers(alternativeNode.get("contentMarkers"), "contentMarkers"),
                    patternMarkers(alternativeNode.get("contentPatternMarkers"), "contentPatternMarkers")));
        }

        Map<String, String> entrypoints = new LinkedHashMap<>();
        JsonNode entrypointsNode = node.get("entrypoints");
        requireObject(entrypointsNode, "entrypoints");
        entrypointsNode.fields().forEachRemaining(entry -> {
            if (!entry.getValue().isTextual()) {
                throw new IllegalArgumentException("entrypoint must be text: " + entry.getKey());
            }
            entrypoints.put(entry.getKey(), entry.getValue().textValue());
        });

        return new PluginDescriptor(
                text(node, "id"),
                PluginKind.fromValue(text(node, "kind")),
                textArray(node.get("requires"), "requires"),
                textArray(node.get("capabilities"), "capabilities").stream()
                        .map(PluginCapability::fromValue).toList(),
                new DetectionRules(
                        textArray(detectionNode.get("extensions"), "extensions"),
                        textArray(detectionNode.get("filesAll"), "filesAll"),
                        textArray(detectionNode.get("filesAny"), "filesAny"),
                        markers,
                        alternatives),
                entrypoints);
    }

    private static List<ContentMarker> markers(JsonNode node, String field) {
        List<ContentMarker> markers = new ArrayList<>();
        for (JsonNode markerNode : requireArray(node, field)) {
            requireObject(markerNode, "content marker");
            requireExactFields(markerNode, MARKER_FIELDS, "content marker");
            markers.add(new ContentMarker(text(markerNode, "path"), text(markerNode, "contains")));
        }
        return List.copyOf(markers);
    }

    private static List<ContentPatternMarker> patternMarkers(JsonNode node, String field) {
        if (node == null) return List.of();
        List<ContentPatternMarker> markers = new ArrayList<>();
        for (JsonNode markerNode : requireArray(node, field)) {
            requireObject(markerNode, "content pattern marker");
            requireExactFields(markerNode, PATTERN_MARKER_FIELDS, "content pattern marker");
            markers.add(new ContentPatternMarker(
                    text(markerNode, "pathPattern"), text(markerNode, "contains")));
        }
        return List.copyOf(markers);
    }

    private static void requireFields(
            JsonNode node, Set<String> required, Set<String> optional, String label) {
        Set<String> actual = new HashSet<>();
        node.fieldNames().forEachRemaining(actual::add);
        Set<String> missing = new java.util.TreeSet<>(required);
        missing.removeAll(actual);
        Set<String> unknown = new java.util.TreeSet<>(actual);
        unknown.removeAll(required);
        unknown.removeAll(optional);
        if (!missing.isEmpty() || !unknown.isEmpty()) {
            throw new IllegalArgumentException(
                    "invalid " + label + " fields: missing=" + missing + ", unknown=" + unknown);
        }
    }

    private static void requireExactFields(JsonNode node, Set<String> expected, String label) {
        Set<String> actual = new HashSet<>();
        node.fieldNames().forEachRemaining(actual::add);
        if (!actual.equals(expected)) {
            Set<String> missing = new java.util.TreeSet<>(expected);
            missing.removeAll(actual);
            Set<String> unknown = new java.util.TreeSet<>(actual);
            unknown.removeAll(expected);
            throw new IllegalArgumentException(
                    "invalid " + label + " fields: missing=" + missing + ", unknown=" + unknown);
        }
    }

    private static void requireObject(JsonNode node, String label) {
        if (node == null || !node.isObject()) throw new IllegalArgumentException(label + " must be an object");
    }

    private static JsonNode requireArray(JsonNode node, String label) {
        if (node == null || !node.isArray()) throw new IllegalArgumentException(label + " must be an array");
        return node;
    }

    private static String text(JsonNode node, String field) {
        JsonNode value = node.get(field);
        if (value == null || !value.isTextual()) {
            throw new IllegalArgumentException(field + " must be text");
        }
        return value.textValue();
    }

    private static List<String> textArray(JsonNode node, String field) {
        requireArray(node, field);
        List<String> values = new ArrayList<>();
        for (JsonNode value : node) {
            if (!value.isTextual()) throw new IllegalArgumentException(field + " must contain text");
            values.add(value.textValue());
        }
        return List.copyOf(values);
    }
}
