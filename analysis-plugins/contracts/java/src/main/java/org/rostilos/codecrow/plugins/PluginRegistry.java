package org.rostilos.codecrow.plugins;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;

import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.PriorityQueue;
import java.util.ServiceLoader;
import java.util.Set;
import java.util.TreeMap;

public final class PluginRegistry {
    private static final ObjectMapper CANONICAL_MAPPER = new ObjectMapper()
            .configure(SerializationFeature.ORDER_MAP_ENTRIES_BY_KEYS, true);

    private final Map<String, PluginDescriptor> byId;
    private final List<String> orderedIds;
    private final String fingerprint;

    public PluginRegistry(Collection<PluginDescriptor> descriptors) {
        TreeMap<String, PluginDescriptor> collected = new TreeMap<>();
        for (PluginDescriptor descriptor : descriptors) {
            if (collected.putIfAbsent(descriptor.id(), descriptor) != null) {
                throw new IllegalArgumentException("duplicate plugin id: " + descriptor.id());
            }
        }
        this.byId = Collections.unmodifiableMap(new LinkedHashMap<>(collected));
        validateRequirements();
        this.orderedIds = topologicalOrder();
        validateFrameworkDependencies();
        this.fingerprint = calculateFingerprint();
    }

    public static PluginRegistry fromPlugins(Collection<? extends CodeCrowPlugin> plugins) {
        return new PluginRegistry(plugins.stream().map(CodeCrowPlugin::descriptor).toList());
    }

    public static PluginRegistry discover(ClassLoader classLoader) {
        List<CodeCrowPlugin> plugins = new ArrayList<>();
        ServiceLoader.load(CodeCrowPlugin.class, classLoader).forEach(plugins::add);
        return fromPlugins(plugins);
    }

    public List<String> orderedIds() {
        return orderedIds;
    }

    public List<PluginDescriptor> descriptors() {
        return orderedIds.stream().map(byId::get).toList();
    }

    public String fingerprint() {
        return fingerprint;
    }

    public String fingerprintFor(Collection<String> pluginIds) {
        return calculateFingerprint(resolve(pluginIds));
    }

    public PluginDescriptor descriptor(String pluginId) {
        PluginDescriptor descriptor = byId.get(pluginId);
        if (descriptor == null) throw new IllegalArgumentException("unknown plugin id: " + pluginId);
        return descriptor;
    }

    public List<PluginDescriptor> resolve(Collection<String> requestedIds) {
        Set<String> closure = new HashSet<>();
        for (String pluginId : requestedIds) include(pluginId, closure);
        return orderedIds.stream().filter(closure::contains).map(byId::get).toList();
    }

    public List<PluginDescriptor> forCapability(
            PluginCapability capability, Collection<String> activeIds) {
        Collection<PluginDescriptor> source = activeIds == null ? descriptors() : resolve(activeIds);
        return source.stream().filter(item -> item.capabilities().contains(capability)).toList();
    }

    private void include(String pluginId, Set<String> closure) {
        PluginDescriptor descriptor = byId.get(pluginId);
        if (descriptor == null) throw new IllegalArgumentException("unknown requested plugin: " + pluginId);
        if (!closure.add(pluginId)) return;
        descriptor.requires().forEach(requirement -> include(requirement, closure));
    }

    private void validateRequirements() {
        for (PluginDescriptor descriptor : byId.values()) {
            List<String> missing = descriptor.requires().stream().filter(id -> !byId.containsKey(id)).toList();
            if (!missing.isEmpty()) {
                throw new IllegalArgumentException(
                        "plugin " + descriptor.id() + " requires missing plugins: " + missing);
            }
        }
    }

    private List<String> topologicalOrder() {
        Map<String, Integer> indegree = new HashMap<>();
        Map<String, List<String>> dependants = new HashMap<>();
        for (PluginDescriptor descriptor : byId.values()) {
            indegree.put(descriptor.id(), descriptor.requires().size());
            for (String requirement : descriptor.requires()) {
                dependants.computeIfAbsent(requirement, ignored -> new ArrayList<>()).add(descriptor.id());
            }
        }
        PriorityQueue<String> ready = new PriorityQueue<>();
        indegree.forEach((id, degree) -> { if (degree == 0) ready.add(id); });
        List<String> ordered = new ArrayList<>();
        while (!ready.isEmpty()) {
            String pluginId = ready.remove();
            ordered.add(pluginId);
            List<String> children = new ArrayList<>(dependants.getOrDefault(pluginId, List.of()));
            Collections.sort(children);
            for (String child : children) {
                int nextDegree = indegree.compute(child, (ignored, degree) -> degree - 1);
                if (nextDegree == 0) ready.add(child);
            }
        }
        if (ordered.size() != byId.size()) {
            List<String> cycleIds = indegree.entrySet().stream()
                    .filter(entry -> entry.getValue() > 0)
                    .map(Map.Entry::getKey).sorted().toList();
            throw new IllegalArgumentException("plugin dependency cycle: " + cycleIds);
        }
        return List.copyOf(ordered);
    }

    private void validateFrameworkDependencies() {
        for (PluginDescriptor descriptor : byId.values()) {
            if (descriptor.kind() == PluginKind.FRAMEWORK
                    && !hasLanguageDependency(descriptor.id(), new HashSet<>())) {
                throw new IllegalArgumentException(
                        "framework plugin " + descriptor.id() + " must depend on a language plugin");
            }
        }
    }

    private boolean hasLanguageDependency(String pluginId, Set<String> visited) {
        if (!visited.add(pluginId)) return false;
        for (String requirement : byId.get(pluginId).requires()) {
            if (byId.get(requirement).kind() == PluginKind.LANGUAGE
                    || hasLanguageDependency(requirement, visited)) return true;
        }
        return false;
    }

    private String calculateFingerprint() {
        return calculateFingerprint(descriptors());
    }

    private String calculateFingerprint(Collection<PluginDescriptor> descriptors) {
        List<Map<String, Object>> projection = new ArrayList<>();
        for (PluginDescriptor descriptor : descriptors) {
            Map<String, Object> item = new TreeMap<>();
            item.put("id", descriptor.id());
            item.put("kind", descriptor.kind().value());
            item.put("requires", descriptor.requires());
            item.put("capabilities", descriptor.capabilities().stream()
                    .map(PluginCapability::value).toList());
            Map<String, Object> detection = new TreeMap<>();
            detection.put("extensions", descriptor.detection().extensions());
            detection.put("filesAll", descriptor.detection().filesAll());
            detection.put("filesAny", descriptor.detection().filesAny());
            detection.put("contentMarkers", descriptor.detection().contentMarkers().stream().map(marker -> {
                Map<String, Object> value = new TreeMap<>();
                value.put("path", marker.path());
                value.put("contains", marker.contains());
                return value;
            }).toList());
            detection.put("alternatives", descriptor.detection().alternatives().stream().map(alternative -> {
                Map<String, Object> value = new TreeMap<>();
                value.put("filesAll", alternative.filesAll());
                value.put("filesAny", alternative.filesAny());
                value.put("pathPatternsAll", alternative.pathPatternsAll());
                value.put("pathPatternsAny", alternative.pathPatternsAny());
                value.put("contentMarkers", alternative.contentMarkers().stream().map(marker -> {
                    Map<String, Object> markerValue = new TreeMap<>();
                    markerValue.put("path", marker.path());
                    markerValue.put("contains", marker.contains());
                    return markerValue;
                }).toList());
                if (!alternative.contentPatternMarkers().isEmpty()) {
                    value.put("contentPatternMarkers", alternative.contentPatternMarkers().stream().map(marker -> {
                        Map<String, Object> markerValue = new TreeMap<>();
                        markerValue.put("pathPattern", marker.pathPattern());
                        markerValue.put("contains", marker.contains());
                        return markerValue;
                    }).toList());
                }
                return value;
            }).toList());
            item.put("detection", detection);
            item.put("entrypoints", descriptor.entrypoints());
            projection.add(item);
        }
        try {
            byte[] encoded = CANONICAL_MAPPER.writeValueAsBytes(projection);
            return "sha256:" + HexFormat.of().formatHex(
                    MessageDigest.getInstance("SHA-256").digest(encoded));
        } catch (Exception exception) {
            throw new IllegalStateException("cannot fingerprint plugin registry", exception);
        }
    }
}
