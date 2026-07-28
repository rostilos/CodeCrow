package org.rostilos.codecrow.plugins;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.TreeSet;

public final class ProjectSelector {
    private static final int MAX_EVIDENCE_PER_PLUGIN = 64;
    private static final ObjectMapper CANONICAL_MAPPER = new ObjectMapper()
            .configure(SerializationFeature.ORDER_MAP_ENTRIES_BY_KEYS, true);

    private final PluginRegistry registry;

    public ProjectSelector(PluginRegistry registry) {
        if (registry == null) throw new IllegalArgumentException("plugin registry is required");
        this.registry = registry;
    }

    public ProjectCapabilities select(RepositoryFacts facts) {
        List<String> selected = new ArrayList<>();
        Map<String, List<String>> evidence = new TreeMap<>();
        for (PluginDescriptor descriptor : registry.descriptors()) {
            List<String> matched = match(descriptor, facts);
            if (matched == null) continue;
            if (!selected.containsAll(descriptor.requires())) continue;
            selected.add(descriptor.id());
            evidence.put(descriptor.id(), matched);
        }

        Map<String, List<String>> filePlugins = new TreeMap<>();
        List<PluginDescriptor> languages = selected.stream()
                .map(registry::descriptor)
                .filter(descriptor -> descriptor.kind() == PluginKind.LANGUAGE)
                .toList();
        for (String path : facts.paths()) {
            String extension = extension(path);
            List<String> matches = languages.stream()
                    .filter(descriptor -> descriptor.detection().extensions().contains(extension))
                    .map(PluginDescriptor::id)
                    .toList();
            if (!matches.isEmpty()) filePlugins.put(path, matches);
        }

        String fingerprint = fingerprint(facts.revision(), selected, filePlugins, evidence);
        return new ProjectCapabilities(
                selected,
                filePlugins,
                evidence,
                List.of(),
                fingerprint,
                registry.fingerprintFor(selected));
    }

    private List<String> match(PluginDescriptor descriptor, RepositoryFacts facts) {
        DetectionRules rules = descriptor.detection();
        List<String> extensionHits = facts.paths().stream()
                .filter(path -> rules.extensions().contains(extension(path)))
                .toList();
        List<DetectionAlternative> groups = new ArrayList<>();
        if (!rules.filesAll().isEmpty() || !rules.filesAny().isEmpty()
                || !rules.contentMarkers().isEmpty()) {
            groups.add(new DetectionAlternative(
                    rules.filesAll(), rules.filesAny(), List.of(), List.of(), rules.contentMarkers()));
        }
        groups.addAll(rules.alternatives());
        TreeSet<String> evidence = new TreeSet<>();
        extensionHits.forEach(path -> evidence.add("extension:" + path));
        boolean groupMatched = false;
        for (DetectionAlternative group : groups) {
            List<String> matched = matchGroup(group, facts);
            if (matched != null) {
                groupMatched = true;
                evidence.addAll(matched);
            }
        }

        if (descriptor.kind() == PluginKind.LANGUAGE) {
            if (extensionHits.isEmpty() && !groupMatched) return null;
        } else if (!groupMatched) return null;
        return evidence.stream().limit(MAX_EVIDENCE_PER_PLUGIN).toList();
    }

    private List<String> matchGroup(DetectionAlternative group, RepositoryFacts facts) {
        Set<String> paths = Set.copyOf(facts.paths());
        if (!paths.containsAll(group.filesAll())) return null;
        if (!group.filesAny().isEmpty() && group.filesAny().stream().noneMatch(paths::contains)) return null;

        Map<String, List<String>> allPatternHits = new TreeMap<>();
        for (String pattern : group.pathPatternsAll()) {
            List<String> hits = facts.paths().stream().filter(path -> PluginGlob.matches(pattern, path)).toList();
            if (hits.isEmpty()) return null;
            allPatternHits.put(pattern, hits);
        }
        Map<String, List<String>> anyPatternHits = new TreeMap<>();
        for (String pattern : group.pathPatternsAny()) {
            List<String> hits = facts.paths().stream().filter(path -> PluginGlob.matches(pattern, path)).toList();
            anyPatternHits.put(pattern, hits);
        }
        if (!group.pathPatternsAny().isEmpty()
                && anyPatternHits.values().stream().allMatch(List::isEmpty)) return null;

        List<ContentMarker> markerHits = group.contentMarkers().stream()
                .filter(marker -> facts.markerContents().containsKey(marker.path()))
                .filter(marker -> facts.markerContents().get(marker.path()).contains(marker.contains()))
                .toList();
        if (markerHits.size() != group.contentMarkers().size()) return null;
        Map<ContentPatternMarker, List<String>> patternMarkerHits = new TreeMap<>();
        for (ContentPatternMarker marker : group.contentPatternMarkers()) {
            List<String> hits = facts.markerContents().entrySet().stream()
                    .filter(entry -> PluginGlob.matches(marker.pathPattern(), entry.getKey()))
                    .filter(entry -> entry.getValue().contains(marker.contains()))
                    .map(Map.Entry::getKey)
                    .toList();
            if (hits.isEmpty()) return null;
            patternMarkerHits.put(marker, hits);
        }

        TreeSet<String> evidence = new TreeSet<>();
        group.filesAll().forEach(path -> evidence.add("file:" + path));
        group.filesAny().stream().filter(paths::contains).forEach(path -> evidence.add("file:" + path));
        for (var entry : allPatternHits.entrySet()) {
            entry.getValue().forEach(path -> evidence.add("pattern:" + entry.getKey() + ":" + path));
        }
        for (var entry : anyPatternHits.entrySet()) {
            entry.getValue().forEach(path -> evidence.add("pattern:" + entry.getKey() + ":" + path));
        }
        markerHits.forEach(marker -> evidence.add("content:" + marker.path() + ":" + marker.contains()));
        patternMarkerHits.forEach((marker, hits) -> hits.forEach(path -> evidence.add(
                "content-pattern:" + marker.pathPattern() + ":" + path + ":" + marker.contains())));
        return List.copyOf(evidence);
    }

    private String fingerprint(
            String revision,
            List<String> selected,
            Map<String, List<String>> filePlugins,
            Map<String, List<String>> evidence) {
        try {
            Map<String, Object> projection = new TreeMap<>();
            projection.put("revision", revision);
            projection.put("registry", registry.fingerprint());
            projection.put("repositoryPlugins", selected);
            projection.put("filePlugins", new LinkedHashMap<>(filePlugins));
            projection.put("detectionEvidence", new LinkedHashMap<>(evidence));
            byte[] encoded = CANONICAL_MAPPER.writeValueAsString(projection)
                    .getBytes(StandardCharsets.UTF_8);
            return "sha256:" + HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(encoded));
        } catch (Exception exception) {
            throw new IllegalStateException("cannot calculate project capability fingerprint", exception);
        }
    }

    private static String extension(String path) {
        int slash = path.lastIndexOf('/');
        int dot = path.lastIndexOf('.');
        return dot > slash ? path.substring(dot).toLowerCase(java.util.Locale.ROOT) : "";
    }
}
