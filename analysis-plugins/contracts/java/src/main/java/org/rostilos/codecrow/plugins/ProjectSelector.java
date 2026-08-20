package org.rostilos.codecrow.plugins;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Collection;
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
        return select(facts, sourcePaths(facts));
    }

    /**
     * Select from complete repository facts while limiting per-file language
     * ownership to the files a host will actually analyze.
     *
     * <p>PR hosts need unchanged marker and path-pattern evidence to select a
     * framework, but must not turn that repository inventory into an
     * enrichment request for every file.</p>
     */
    public ProjectCapabilities select(
            RepositoryFacts facts,
            Collection<String> fileAssignmentPaths) {
        if (facts == null) throw new IllegalArgumentException("repository facts are required");
        if (fileAssignmentPaths == null) {
            throw new IllegalArgumentException("file assignment paths are required");
        }
        List<String> assignmentPaths = sourcePaths(
                List.copyOf(fileAssignmentPaths), facts.sourceRoot());
        if (facts.projectType() != null) return selectExplicit(facts, assignmentPaths);
        List<String> selected = new ArrayList<>();
        Map<String, List<String>> evidence = new TreeMap<>();
        Set<String> repositoryPathSet = Set.copyOf(facts.paths());
        for (PluginDescriptor descriptor : registry.descriptors()) {
            List<String> matched = match(descriptor, facts, repositoryPathSet);
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
        for (String path : assignmentPaths) {
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

    private ProjectCapabilities selectExplicit(
            RepositoryFacts facts,
            List<String> assignmentPaths) {
        PluginDescriptor requested = registry.descriptor(facts.projectType());
        TreeSet<String> requestedIds = new TreeSet<>();
        requestedIds.add(requested.id());
        for (PluginDescriptor descriptor : registry.descriptors()) {
            if (descriptor.kind() != PluginKind.LANGUAGE) continue;
            if (assignmentPaths.stream().anyMatch(path ->
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
        for (String path : assignmentPaths) {
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

    private List<String> match(
            PluginDescriptor descriptor,
            RepositoryFacts facts,
            Set<String> repositoryPaths) {
        DetectionRules rules = descriptor.detection();
        List<String> extensionHits = sourcePaths(facts).stream()
                .filter(path -> rules.extensions().contains(extension(path)))
                .limit(MAX_EVIDENCE_PER_PLUGIN)
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
            List<String> matched = matchGroup(group, facts, repositoryPaths);
            if (matched != null) {
                groupMatched = true;
                evidence.addAll(matched);
            }
        }

        if (descriptor.kind() == PluginKind.LANGUAGE) {
            if (extensionHits.isEmpty() && !groupMatched) return null;
        } else if (!groupMatched) return null;
        return boundedEvidence(evidence);
    }

    private static List<String> sourcePaths(RepositoryFacts facts) {
        if (facts.sourceRoot() == null) return facts.paths();
        return sourcePaths(facts.paths(), facts.sourceRoot());
    }

    private static List<String> sourcePaths(
            Collection<String> paths,
            String sourceRoot) {
        if (sourceRoot == null) return List.copyOf(paths);
        String prefix = sourceRoot + "/";
        return paths.stream()
                .filter(path -> path.equals(sourceRoot) || path.startsWith(prefix))
                .toList();
    }

    private List<String> matchGroup(
            DetectionAlternative group,
            RepositoryFacts facts,
            Set<String> paths) {
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

        TreeSet<String> matchedEvidence = new TreeSet<>();
        int matchedRoots = 0;
        for (String root : candidateRoots) {
            List<String> filesAll = group.filesAll().stream()
                    .map(relative -> rooted(root, relative)).toList();
            if (!paths.containsAll(filesAll)) continue;
            List<String> filesAny = group.filesAny().stream()
                    .map(relative -> rooted(root, relative)).filter(paths::contains).toList();
            if (!group.filesAny().isEmpty() && filesAny.isEmpty()) continue;

            List<String> rootedPaths = pathsUnderRoot(facts.paths(), root);
            Map<String, List<String>> allPatternHits = patternHits(
                    group.pathPatternsAll(), rootedPaths, root);
            if (allPatternHits.values().stream().anyMatch(List::isEmpty)) continue;
            Map<String, List<String>> anyPatternHits = patternHits(
                    group.pathPatternsAny(), rootedPaths, root);
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
            matchedEvidence.addAll(evidence);
            matchedRoots++;
            if (matchedRoots == MAX_EVIDENCE_PER_PLUGIN) break;
        }
        return matchedEvidence.isEmpty() ? null : boundedEvidence(matchedEvidence);
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
                    .limit(MAX_EVIDENCE_PER_PLUGIN)
                    .toList());
        }
        return result;
    }

    private static List<String> pathsUnderRoot(List<String> paths, String root) {
        if (root.isEmpty()) return paths;
        String prefix = root + "/";
        int start = Collections.binarySearch(paths, prefix);
        if (start < 0) start = -start - 1;
        int end = start;
        while (end < paths.size() && paths.get(end).startsWith(prefix)) end++;
        return paths.subList(start, end);
    }

    private static List<String> boundedEvidence(Collection<String> evidence) {
        TreeSet<String> ordered = new TreeSet<>(evidence);
        List<String> roots = ordered.stream()
                .filter(item -> item.startsWith("root:"))
                .limit(MAX_EVIDENCE_PER_PLUGIN)
                .toList();
        int remaining = MAX_EVIDENCE_PER_PLUGIN - roots.size();
        TreeSet<String> retained = new TreeSet<>(roots);
        ordered.stream()
                .filter(item -> !item.startsWith("root:"))
                .limit(remaining)
                .forEach(retained::add);
        return List.copyOf(retained);
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
