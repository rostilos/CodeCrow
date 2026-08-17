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
        if (facts.projectType() != null) return selectExplicit(facts);
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

    private ProjectCapabilities selectExplicit(RepositoryFacts facts) {
        PluginDescriptor requested = registry.descriptor(facts.projectType());
        TreeSet<String> requestedIds = new TreeSet<>();
        requestedIds.add(requested.id());
        for (PluginDescriptor descriptor : registry.descriptors()) {
            if (descriptor.kind() != PluginKind.LANGUAGE) continue;
            if (facts.paths().stream().anyMatch(path ->
                    descriptor.detection().extensions().contains(extension(path)))) {
                requestedIds.add(descriptor.id());
            }
        }
        List<PluginDescriptor> resolved = registry.resolve(requestedIds);
        List<String> selected = resolved.stream().map(PluginDescriptor::id).toList();
        Map<String, List<String>> evidence = new TreeMap<>();
        for (String pluginId : selected) {
            evidence.put(pluginId, new TreeSet<>(List.of(
                    pluginId.equals(requested.id())
                            ? "manual-project-type:" + requested.id()
                            : "manual-project-type-dependency:" + requested.id(),
                    "root:" + (facts.sourceRoot() == null ? "." : facts.sourceRoot())
            )).stream().toList());
        }
        Map<String, List<String>> filePlugins = new TreeMap<>();
        List<PluginDescriptor> languages = resolved.stream()
                .filter(descriptor -> descriptor.kind() == PluginKind.LANGUAGE)
                .toList();
        for (String path : facts.paths()) {
            List<String> matches = languages.stream()
                    .filter(descriptor -> descriptor.detection().extensions().contains(extension(path)))
                    .map(PluginDescriptor::id)
                    .toList();
            if (!matches.isEmpty()) filePlugins.put(path, matches);
        }
        return new ProjectCapabilities(
                selected,
                filePlugins,
                evidence,
                List.of(),
                fingerprint(facts.revision(), selected, filePlugins, evidence),
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
        List<Set<String>> rootSets = new ArrayList<>();
        group.filesAll().forEach(relative -> rootSets.add(suffixRoots(facts.paths(), relative)));
        group.contentMarkers().forEach(marker -> rootSets.add(facts.markerContents().entrySet().stream()
                .filter(entry -> entry.getValue().contains(marker.contains()))
                .flatMap(entry -> suffixRoots(List.of(entry.getKey()), marker.path()).stream())
                .collect(java.util.stream.Collectors.toSet())));
        Set<String> candidateRoots = new TreeSet<>();
        if (!rootSets.isEmpty()) {
            candidateRoots.addAll(rootSets.get(0));
            rootSets.subList(1, rootSets.size()).forEach(candidateRoots::retainAll);
        } else if (!group.filesAny().isEmpty()) {
            group.filesAny().forEach(relative -> candidateRoots.addAll(suffixRoots(facts.paths(), relative)));
        } else {
            candidateRoots.add(facts.sourceRoot() == null ? "" : facts.sourceRoot());
        }
        if (facts.sourceRoot() != null) {
            candidateRoots.retainAll(Set.of(facts.sourceRoot()));
        }

        for (String root : candidateRoots) {
            List<String> filesAll = group.filesAll().stream()
                    .map(relative -> rooted(root, relative)).toList();
            if (!paths.containsAll(filesAll)) continue;
            List<String> filesAny = group.filesAny().stream()
                    .map(relative -> rooted(root, relative)).filter(paths::contains).toList();
            if (!group.filesAny().isEmpty() && filesAny.isEmpty()) continue;

            Map<String, List<String>> allPatternHits = patternHits(group.pathPatternsAll(), facts.paths(), root);
            if (allPatternHits.values().stream().anyMatch(List::isEmpty)) continue;
            Map<String, List<String>> anyPatternHits = patternHits(group.pathPatternsAny(), facts.paths(), root);
            if (!group.pathPatternsAny().isEmpty()
                    && anyPatternHits.values().stream().allMatch(List::isEmpty)) continue;

            Map<ContentMarker, String> markerHits = new LinkedHashMap<>();
            for (ContentMarker marker : group.contentMarkers()) {
                String path = rooted(root, marker.path());
                if (!facts.markerContents().getOrDefault(path, "").contains(marker.contains())) break;
                markerHits.put(marker, path);
            }
            if (markerHits.size() != group.contentMarkers().size()) continue;
            Map<ContentPatternMarker, List<String>> patternMarkerHits = new TreeMap<>();
            for (ContentPatternMarker marker : group.contentPatternMarkers()) {
                List<String> hits = facts.markerContents().entrySet().stream()
                        .filter(entry -> relativeToRoot(entry.getKey(), root) != null)
                        .filter(entry -> PluginGlob.matches(marker.pathPattern(), relativeToRoot(entry.getKey(), root)))
                        .filter(entry -> entry.getValue().contains(marker.contains()))
                        .map(Map.Entry::getKey).toList();
                if (hits.isEmpty()) break;
                patternMarkerHits.put(marker, hits);
            }
            if (patternMarkerHits.size() != group.contentPatternMarkers().size()) continue;

            TreeSet<String> evidence = new TreeSet<>();
            evidence.add("root:" + (root.isEmpty() ? "." : root));
            filesAll.forEach(path -> evidence.add("file:" + path));
            filesAny.forEach(path -> evidence.add("file:" + path));
            for (var entry : allPatternHits.entrySet()) entry.getValue().forEach(path ->
                    evidence.add("pattern:" + entry.getKey() + ":" + path));
            for (var entry : anyPatternHits.entrySet()) entry.getValue().forEach(path ->
                    evidence.add("pattern:" + entry.getKey() + ":" + path));
            markerHits.forEach((marker, path) ->
                    evidence.add("content:" + path + ":" + marker.contains()));
            patternMarkerHits.forEach((marker, hits) -> hits.forEach(path -> evidence.add(
                    "content-pattern:" + marker.pathPattern() + ":" + path + ":" + marker.contains())));
            return List.copyOf(evidence);
        }
        return null;
    }

    private static Set<String> suffixRoots(List<String> paths, String relative) {
        TreeSet<String> roots = new TreeSet<>();
        for (String path : paths) {
            if (path.equals(relative)) roots.add("");
            else if (path.endsWith("/" + relative)) {
                roots.add(path.substring(0, path.length() - relative.length() - 1));
            }
        }
        return roots;
    }

    private static String rooted(String root, String relative) {
        return root.isEmpty() ? relative : root + "/" + relative;
    }

    private static String relativeToRoot(String path, String root) {
        if (root.isEmpty()) return path;
        String prefix = root + "/";
        return path.startsWith(prefix) ? path.substring(prefix.length()) : null;
    }

    private static Map<String, List<String>> patternHits(
            List<String> patterns, List<String> paths, String root) {
        Map<String, List<String>> result = new TreeMap<>();
        for (String pattern : patterns) {
            result.put(pattern, paths.stream()
                    .filter(path -> relativeToRoot(path, root) != null)
                    .filter(path -> PluginGlob.matches(pattern, relativeToRoot(path, root)))
                    .toList());
        }
        return result;
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
