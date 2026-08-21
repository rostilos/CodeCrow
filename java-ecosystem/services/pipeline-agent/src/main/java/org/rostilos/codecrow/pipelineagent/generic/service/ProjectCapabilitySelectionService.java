package org.rostilos.codecrow.pipelineagent.generic.service;

import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.PrEnrichmentDataDto;
import org.rostilos.codecrow.core.model.project.config.AnalysisProfileConfig;
import org.rostilos.codecrow.plugins.ContentMarker;
import org.rostilos.codecrow.plugins.ContentPatternMarker;
import org.rostilos.codecrow.plugins.FileDisposition;
import org.rostilos.codecrow.plugins.PluginGlob;
import org.rostilos.codecrow.plugins.PluginDescriptor;
import org.rostilos.codecrow.plugins.PluginRegistry;
import org.rostilos.codecrow.plugins.PluginRuntime;
import org.rostilos.codecrow.plugins.ProjectCapabilities;
import org.rostilos.codecrow.plugins.ProjectSelector;
import org.rostilos.codecrow.plugins.RepositoryFacts;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.TreeSet;

@Service
public class ProjectCapabilitySelectionService {
    private static final Logger log = LoggerFactory.getLogger(ProjectCapabilitySelectionService.class);
    private static final int MAX_MARKER_FILES = 64;
    private static final int MAX_MARKER_BYTES = 262_144;
    private static final int MAX_REPOSITORY_FILES = 500_000;

    private final PluginRuntime runtime;
    private final PluginRegistry registry;
    private final ProjectSelector selector;

    public ProjectCapabilitySelectionService() {
        this(PluginRuntime.discover(Thread.currentThread().getContextClassLoader()));
    }

    ProjectCapabilitySelectionService(PluginRuntime runtime) {
        if (runtime == null) throw new IllegalArgumentException("plugin runtime is required");
        this.runtime = runtime;
        registry = runtime.registry();
        selector = new ProjectSelector(registry);
        log.info("Discovered plugins: {}", registry.orderedIds());
    }

    /**
     * Acquire immutable repository markers once and apply any file-policy
     * contributors that can already be selected from path/marker evidence.
     */
    public SelectionPlan plan(
            VcsClient vcsClient,
            String workspace,
            String repository,
            String commit,
            List<String> changedFiles) {
        return plan(vcsClient, workspace, repository, commit, changedFiles, null);
    }

    public SelectionPlan plan(
            VcsClient vcsClient,
            String workspace,
            String repository,
            String commit,
            List<String> changedFiles,
            AnalysisProfileConfig analysisProfile) {
        TreeSet<String> changedPaths = new TreeSet<>();
        if (changedFiles != null) {
            changedFiles.stream().map(ProjectCapabilitySelectionService::normalize)
                    .forEach(changedPaths::add);
        }
        TreeSet<String> paths = new TreeSet<>();

        String projectType = analysisProfile != null ? analysisProfile.projectType() : null;
        String sourceRoot = analysisProfile != null ? analysisProfile.sourceRoot() : null;
        boolean completeRepositoryInventory = false;
        if (projectType == null && !registry.descriptors().isEmpty()) {
            try {
                List<String> repositoryFiles = vcsClient.listRepositoryFiles(
                        workspace,
                        repository,
                        commit,
                        MAX_REPOSITORY_FILES);
                if (repositoryFiles != null) {
                    repositoryFiles.stream()
                            .filter(path -> path != null && !path.isBlank())
                            .map(ProjectCapabilitySelectionService::normalize)
                            .forEach(paths::add);
                    completeRepositoryInventory = true;
                } else {
                    log.warn(
                            "Repository path inventory at commit {} was unavailable; automatic "
                                    + "plugin detection will continue with reduced evidence",
                            commit);
                }
            } catch (Exception exception) {
                log.warn(
                        "Cannot list the pinned repository tree at commit {}; automatic plugin "
                                + "detection will continue with changed paths and exact marker reads: {}",
                        commit,
                        exception.getMessage());
            }
        }
        if (!completeRepositoryInventory) {
            // Changed paths are only a fallback existence signal. A complete
            // pinned tree is authoritative and deliberately excludes deleted
            // paths and old rename sides from repository facts.
            paths.addAll(changedPaths);
        }

        TreeSet<String> markerPaths = new TreeSet<>();
        TreeSet<ContentPatternMarker> patternMarkers = new TreeSet<>();
        List<MarkerReadRule> markerReadRules = new ArrayList<>();
        if (projectType == null) {
            for (PluginDescriptor descriptor : registry.descriptors()) {
                markerPaths.addAll(descriptor.detection().filesAll());
                markerPaths.addAll(descriptor.detection().filesAny());
                for (String path : descriptor.detection().filesAll()) {
                    markerReadRules.add(MarkerReadRule.exact(
                            descriptor.id(), "files-all:" + path, path, false));
                }
                for (String path : descriptor.detection().filesAny()) {
                    markerReadRules.add(MarkerReadRule.exact(
                            descriptor.id(), "files-any:" + path, path, false));
                }
                for (ContentMarker marker : descriptor.detection().contentMarkers()) {
                    markerPaths.add(marker.path());
                    markerReadRules.add(MarkerReadRule.exact(
                            descriptor.id(),
                            "content:" + marker.path() + ":" + marker.contains(),
                            marker.path(),
                            true));
                }
                int alternativeIndex = 0;
                for (var alternative : descriptor.detection().alternatives()) {
                    String scope = "alternative-" + alternativeIndex++ + ":";
                    markerPaths.addAll(alternative.filesAll());
                    markerPaths.addAll(alternative.filesAny());
                    for (String path : alternative.filesAll()) {
                        markerReadRules.add(MarkerReadRule.exact(
                                descriptor.id(), scope + "files-all:" + path, path, false));
                    }
                    for (String path : alternative.filesAny()) {
                        markerReadRules.add(MarkerReadRule.exact(
                                descriptor.id(), scope + "files-any:" + path, path, false));
                    }
                    for (ContentMarker marker : alternative.contentMarkers()) {
                        markerPaths.add(marker.path());
                        markerReadRules.add(MarkerReadRule.exact(
                                descriptor.id(),
                                scope + "content:" + marker.path() + ":" + marker.contains(),
                                marker.path(),
                                true));
                    }
                    for (ContentPatternMarker marker : alternative.contentPatternMarkers()) {
                        patternMarkers.add(marker);
                        markerReadRules.add(MarkerReadRule.pattern(
                                descriptor.id(),
                                scope + "pattern:" + marker.pathPattern()
                                        + ":" + marker.contains(),
                                marker));
                    }
                }
            }
        }
        TreeSet<String> candidateRoots = new TreeSet<>();
        if (sourceRoot != null) {
            candidateRoots.add(sourceRoot);
        } else {
            candidateRoots.add("");
            Map<String, List<String>> rootMarkersByName = new LinkedHashMap<>();
            for (String markerPath : markerPaths) {
                rootMarkersByName
                        .computeIfAbsent(baseName(markerPath), ignored -> new ArrayList<>())
                        .add(markerPath);
            }
            for (String repositoryPath : paths) {
                for (String markerPath : rootMarkersByName.getOrDefault(
                        baseName(repositoryPath), List.of())) {
                    String root = rootForMarker(repositoryPath, markerPath);
                    if (root != null) candidateRoots.add(root);
                }
            }
            if (!completeRepositoryInventory) {
                // Without a provider tree, ancestor roots at least let an
                // ordinary changed framework file discover unchanged exact
                // markers. Glob-only context still correctly remains reduced.
                changedPaths.forEach(path -> addAncestorRoots(candidateRoots, path));
            }
        }

        Comparator<MarkerCandidate> candidateOrder = markerCandidateOrder(changedPaths);
        Map<MarkerLaneKey, BoundedMarkerLane> markerLanes = new LinkedHashMap<>();
        boolean candidateDiscoveryReduced = false;
        if (completeRepositoryInventory) {
            Map<String, List<MarkerReadRule>> exactRulesByName = new LinkedHashMap<>();
            List<MarkerReadRule> patternRules = new ArrayList<>();
            for (MarkerReadRule rule : markerReadRules) {
                if (rule.exactPath() != null && rule.contentRequired()) {
                    exactRulesByName
                            .computeIfAbsent(baseName(rule.exactPath()),
                                    ignored -> new ArrayList<>())
                            .add(rule);
                } else if (rule.pattern() != null) {
                    patternRules.add(rule);
                }
            }
            for (String repositoryPath : paths) {
                for (MarkerReadRule rule : exactRulesByName.getOrDefault(
                        baseName(repositoryPath), List.of())) {
                    String root = rootForMarker(repositoryPath, rule.exactPath());
                    if (root == null || !candidateRoots.contains(root)) continue;
                    candidateDiscoveryReduced |= offerCandidate(
                            markerLanes,
                            rule,
                            new MarkerCandidate(root, repositoryPath),
                            candidateOrder);
                }

                if (candidateRoots.contains("")) {
                    candidateDiscoveryReduced |= offerPatternCandidates(
                            markerLanes,
                            patternRules,
                            "",
                            repositoryPath,
                            repositoryPath,
                            candidateOrder);
                }
                int slash = repositoryPath.indexOf('/');
                while (slash > 0) {
                    String root = repositoryPath.substring(0, slash);
                    if (candidateRoots.contains(root)) {
                        candidateDiscoveryReduced |= offerPatternCandidates(
                                markerLanes,
                                patternRules,
                                root,
                                repositoryPath.substring(slash + 1),
                                repositoryPath,
                                candidateOrder);
                    }
                    slash = repositoryPath.indexOf('/', slash + 1);
                }
            }
        } else {
            for (String root : candidateRoots) {
                for (MarkerReadRule rule : markerReadRules) {
                    if (rule.exactPath() == null) continue;
                    candidateDiscoveryReduced |= offerCandidate(
                            markerLanes,
                            rule,
                            new MarkerCandidate(root, rooted(root, rule.exactPath())),
                            candidateOrder);
                }
            }
        }
        MarkerSchedule markerSchedule = scheduleMarkerPaths(
                markerLanes.values(), candidateOrder, MAX_MARKER_FILES);
        List<String> scheduledMarkerPaths = markerSchedule.paths();
        if (candidateDiscoveryReduced || markerSchedule.omittedCandidates()) {
            log.warn(
                    "Some plugin marker candidates were skipped after reaching the {}-file host "
                            + "budget; automatic plugin detection will continue with reduced evidence",
                    MAX_MARKER_FILES);
        }

        Map<String, String> markerContents = new LinkedHashMap<>();
        int consumed = 0;
        int skippedForBytes = 0;
        for (String markerPath : scheduledMarkerPaths) {
            try {
                String content = vcsClient.getFileContent(
                        workspace, repository, markerPath, commit);
                if (content == null) continue;
                // A successful pinned read proves path existence even when
                // the content cannot fit in the optional marker byte budget.
                paths.add(markerPath);
                int bytes = content.getBytes(java.nio.charset.StandardCharsets.UTF_8).length;
                if (consumed + bytes > MAX_MARKER_BYTES) {
                    skippedForBytes++;
                    continue;
                }
                consumed += bytes;
                markerContents.put(markerPath, content);
            } catch (IOException exception) {
                log.warn(
                        "Cannot read plugin marker {} from the pinned repository snapshot; "
                                + "automatic plugin detection will continue with reduced evidence",
                        markerPath,
                        exception);
            }
        }
        if (skippedForBytes > 0) {
            log.warn(
                    "Skipped {} plugin marker content file(s) after reaching the {}-byte host "
                            + "budget; automatic plugin detection will continue with reduced evidence",
                    skippedForBytes,
                    MAX_MARKER_BYTES);
        }

        RepositoryFacts repositoryFacts = new RepositoryFacts(
                commit, List.copyOf(paths), markerContents,
                projectType, sourceRoot);
        ProjectCapabilities preliminary = selector.select(
                repositoryFacts, changedPaths);
        List<String> enrichmentPaths = filterEnrichmentPaths(preliminary, changedFiles);
        return new SelectionPlan(
                commit,
                List.copyOf(paths),
                Map.copyOf(markerContents),
                List.copyOf(patternMarkers),
                consumed,
                preliminary,
                enrichmentPaths,
                projectType,
                sourceRoot,
                completeRepositoryInventory);
    }

    /**
     * Complete selection with content-pattern evidence obtained from the
     * already policy-filtered enrichment result.
     */
    public ProjectCapabilities complete(
            SelectionPlan plan,
            PrEnrichmentDataDto enrichment) {
        if (plan == null) throw new IllegalArgumentException("selection plan is required");
        TreeSet<String> paths = new TreeSet<>(plan.repositoryPaths());
        Map<String, String> markerContents = new LinkedHashMap<>(plan.markerContents());
        int consumed = plan.markerBytes();
        int skippedForBytes = 0;
        boolean skippedForFiles = false;
        if (enrichment != null && enrichment.fileContents() != null) {
            if (!plan.completeRepositoryInventory()) {
                enrichment.fileContents().stream()
                        .filter(file -> !file.skipped() && file.content() != null)
                        .map(file -> normalize(file.path()))
                        .forEach(paths::add);
            }
            Map<ContentPatternMarker, TreeMap<String, String>> patternCandidates =
                    new TreeMap<>();
            for (ContentPatternMarker marker : plan.patternMarkers()) {
                TreeMap<String, String> candidates = patternCandidates.computeIfAbsent(
                        marker, ignored -> new TreeMap<>());
                for (var file : enrichment.fileContents()) {
                    if (file.skipped() || file.content() == null) continue;
                    String path = normalize(file.path());
                    if (markerContents.containsKey(path)) continue;
                    if (plan.completeRepositoryInventory() && !paths.contains(path)) continue;
                    if (!matchesPatternCandidate(marker, path, plan.sourceRoot())
                            || !file.content().contains(marker.contains())) {
                        continue;
                    }
                    candidates.putIfAbsent(path, file.content());
                    if (candidates.size() > MAX_MARKER_FILES) {
                        candidates.pollLastEntry();
                        skippedForFiles = true;
                    }
                }
            }
            PatternMarkerSchedule patternSchedule = schedulePatternMarkerPaths(
                    patternCandidates,
                    markerContents.keySet(),
                    Math.max(0, MAX_MARKER_FILES - markerContents.size()));
            skippedForFiles |= patternSchedule.omittedCandidates();
            for (Map.Entry<String, String> candidate : patternSchedule.files().entrySet()) {
                String path = candidate.getKey();
                String content = candidate.getValue();
                int bytes = content.getBytes(java.nio.charset.StandardCharsets.UTF_8).length;
                if (consumed + bytes > MAX_MARKER_BYTES) {
                    skippedForBytes++;
                    continue;
                }
                consumed += bytes;
                markerContents.put(path, content);
            }
        }
        if (skippedForFiles) {
            log.warn(
                    "Some content-pattern marker files were skipped after reaching the {}-file "
                            + "host budget; automatic plugin detection will continue with reduced "
                            + "evidence",
                    MAX_MARKER_FILES);
        }
        if (skippedForBytes > 0) {
            log.warn(
                    "Skipped {} content-pattern marker file(s) after reaching the {}-byte host "
                            + "budget; automatic plugin detection will continue with reduced evidence",
                    skippedForBytes,
                    MAX_MARKER_BYTES);
        }

        return selector.select(
                new RepositoryFacts(
                        plan.commit(), List.copyOf(paths), markerContents,
                        plan.projectType(), plan.sourceRoot()),
                plan.enrichmentPaths());
    }

    /**
     * Compatibility entry point for callers that do not need pre-enrichment
     * filtering.
     */
    public ProjectCapabilities select(
            VcsClient vcsClient,
            String workspace,
            String repository,
            String commit,
            List<String> changedFiles,
            PrEnrichmentDataDto enrichment) {
        return complete(
                plan(vcsClient, workspace, repository, commit, changedFiles),
                enrichment);
    }

    public List<String> filterEnrichmentPaths(
            ProjectCapabilities capabilities,
            Collection<String> paths) {
        if (paths == null || paths.isEmpty()) return List.of();
        TreeSet<String> retained = new TreeSet<>();
        for (String path : paths) {
            String normalized = normalize(path);
            FileDisposition disposition = runtime.fileDisposition(normalized, capabilities);
            if (disposition != FileDisposition.EXCLUDED
                    && disposition != FileDisposition.GENERATED) {
                retained.add(normalized);
            }
        }
        return List.copyOf(retained);
    }

    public PluginRegistry registry() {
        return registry;
    }

    private static void addAncestorRoots(Set<String> roots, String path) {
        int slash = path.lastIndexOf('/');
        while (slash > 0) {
            roots.add(path.substring(0, slash));
            slash = path.lastIndexOf('/', slash - 1);
        }
    }

    private static String rooted(String root, String relative) {
        return root == null || root.isBlank() ? relative : root + "/" + relative;
    }

    private static String baseName(String path) {
        int slash = path.lastIndexOf('/');
        return slash < 0 ? path : path.substring(slash + 1);
    }

    private static String rootForMarker(String repositoryPath, String markerPath) {
        if (repositoryPath.equals(markerPath)) return "";
        String suffix = "/" + markerPath;
        if (!repositoryPath.endsWith(suffix)) return null;
        return repositoryPath.substring(0, repositoryPath.length() - suffix.length());
    }

    private static boolean offerPatternCandidates(
            Map<MarkerLaneKey, BoundedMarkerLane> lanes,
            List<MarkerReadRule> rules,
            String root,
            String relativePath,
            String repositoryPath,
            Comparator<MarkerCandidate> candidateOrder) {
        boolean reduced = false;
        for (MarkerReadRule rule : rules) {
            if (PluginGlob.matches(rule.pattern().pathPattern(), relativePath)) {
                reduced |= offerCandidate(
                        lanes,
                        rule,
                        new MarkerCandidate(root, repositoryPath),
                        candidateOrder);
            }
        }
        return reduced;
    }

    private static boolean offerCandidate(
            Map<MarkerLaneKey, BoundedMarkerLane> lanes,
            MarkerReadRule rule,
            MarkerCandidate candidate,
            Comparator<MarkerCandidate> candidateOrder) {
        MarkerLaneKey key = new MarkerLaneKey(rule.pluginId(), rule.condition());
        BoundedMarkerLane lane = lanes.computeIfAbsent(
                key, ignored -> new BoundedMarkerLane(key, candidateOrder));
        return lane.offer(candidate, MAX_MARKER_FILES);
    }

    private static Comparator<MarkerCandidate> markerCandidateOrder(
            Set<String> changedPaths) {
        return Comparator
                .comparing((MarkerCandidate candidate) ->
                        !changedPaths.contains(candidate.path()))
                .thenComparing(candidate ->
                        !rootContainsChangedPath(candidate.root(), changedPaths))
                // The repository root contains every changed path. Retain its
                // conventional marker before speculative nested ancestors can
                // consume the bounded lane.
                .thenComparing(candidate -> !candidate.root().isBlank())
                .thenComparing(
                        (MarkerCandidate candidate) -> candidate.root().length(),
                        Comparator.reverseOrder())
                .thenComparing(MarkerCandidate::path)
                .thenComparing(MarkerCandidate::root);
    }

    private static MarkerSchedule scheduleMarkerPaths(
            Collection<BoundedMarkerLane> lanes,
            Comparator<MarkerCandidate> candidateOrder,
            int limit) {
        Map<String, List<MarkerLaneCursor>> lanesByPlugin = new LinkedHashMap<>();
        for (BoundedMarkerLane lane : lanes) {
            if (lane.candidates().isEmpty()) continue;
            lanesByPlugin
                    .computeIfAbsent(lane.key().pluginId(), ignored -> new ArrayList<>())
                    .add(new MarkerLaneCursor(lane));
        }
        List<PluginLaneCursor> plugins = new ArrayList<>();
        for (Map.Entry<String, List<MarkerLaneCursor>> entry : lanesByPlugin.entrySet()) {
            entry.getValue().sort(Comparator
                    .comparing(MarkerLaneCursor::first, candidateOrder)
                    .thenComparing(cursor -> cursor.key().condition()));
            plugins.add(new PluginLaneCursor(entry.getKey(), entry.getValue()));
        }
        plugins.sort(Comparator
                .comparing(PluginLaneCursor::bestCandidate, candidateOrder)
                .thenComparing(PluginLaneCursor::pluginId));

        LinkedHashSet<String> selected = new LinkedHashSet<>();
        boolean progressed = true;
        while (selected.size() < limit && progressed) {
            progressed = false;
            for (PluginLaneCursor plugin : plugins) {
                if (selected.size() >= limit) break;
                String candidate = plugin.next(selected);
                if (candidate == null) continue;
                selected.add(candidate);
                progressed = true;
            }
        }
        boolean omittedCandidates = lanes.stream()
                .flatMap(lane -> lane.candidates().stream())
                .map(MarkerCandidate::path)
                .anyMatch(path -> !selected.contains(path));
        return new MarkerSchedule(List.copyOf(selected), omittedCandidates);
    }

    private static PatternMarkerSchedule schedulePatternMarkerPaths(
            Map<ContentPatternMarker, TreeMap<String, String>> candidatesByPattern,
            Set<String> existingPaths,
            int limit) {
        List<java.util.Iterator<Map.Entry<String, String>>> lanes = candidatesByPattern.values()
                .stream()
                .map(candidates -> candidates.entrySet().iterator())
                .toList();
        LinkedHashMap<String, String> selected = new LinkedHashMap<>();
        boolean progressed = true;
        while (selected.size() < limit && progressed) {
            progressed = false;
            for (var lane : lanes) {
                while (lane.hasNext()) {
                    Map.Entry<String, String> candidate = lane.next();
                    if (existingPaths.contains(candidate.getKey())
                            || selected.containsKey(candidate.getKey())) {
                        continue;
                    }
                    selected.put(candidate.getKey(), candidate.getValue());
                    progressed = true;
                    break;
                }
                if (selected.size() >= limit) break;
            }
        }
        boolean omittedCandidates = candidatesByPattern.values().stream()
                .flatMap(candidates -> candidates.keySet().stream())
                .anyMatch(path -> !existingPaths.contains(path) && !selected.containsKey(path));
        return new PatternMarkerSchedule(
                java.util.Collections.unmodifiableMap(new LinkedHashMap<>(selected)),
                omittedCandidates);
    }

    private static boolean rootContainsChangedPath(
            String root,
            Set<String> changedPaths) {
        if (root == null || root.isBlank()) return !changedPaths.isEmpty();
        String prefix = root + "/";
        return changedPaths.stream()
                .anyMatch(path -> path.equals(root) || path.startsWith(prefix));
    }

    private static String normalize(String path) {
        String normalized = path.replace('\\', '/');
        while (normalized.startsWith("./")) normalized = normalized.substring(2);
        while (normalized.startsWith("/")) normalized = normalized.substring(1);
        return normalized;
    }

    private static String relativeToRoot(String path, String root) {
        if (root == null || root.isBlank()) return path;
        String prefix = root + "/";
        return path.startsWith(prefix) ? path.substring(prefix.length()) : null;
    }

    private static boolean matchesPatternCandidate(
            ContentPatternMarker marker,
            String path,
            String sourceRoot) {
        String relative = relativeToRoot(path, sourceRoot);
        if (relative == null) return false;
        if (PluginGlob.matches(marker.pathPattern(), relative)) return true;
        if (sourceRoot != null && !sourceRoot.isBlank()) return false;
        int slash = relative.indexOf('/');
        while (slash >= 0) {
            relative = relative.substring(slash + 1);
            if (PluginGlob.matches(marker.pathPattern(), relative)) return true;
            slash = relative.indexOf('/');
        }
        return false;
    }

    private record MarkerReadRule(
            String pluginId,
            String condition,
            String exactPath,
            ContentPatternMarker pattern,
            boolean contentRequired) {

        private static MarkerReadRule exact(
                String pluginId,
                String condition,
                String path,
                boolean contentRequired) {
            return new MarkerReadRule(
                    pluginId, condition, path, null, contentRequired);
        }

        private static MarkerReadRule pattern(
                String pluginId,
                String condition,
                ContentPatternMarker pattern) {
            return new MarkerReadRule(
                    pluginId, condition, null, pattern, true);
        }
    }

    private record MarkerLaneKey(String pluginId, String condition) {}

    private record MarkerCandidate(String root, String path) {}

    private record MarkerSchedule(
            List<String> paths,
            boolean omittedCandidates) {}

    private record PatternMarkerSchedule(
            Map<String, String> files,
            boolean omittedCandidates) {}

    private static final class BoundedMarkerLane {
        private final MarkerLaneKey key;
        private final Comparator<MarkerCandidate> order;
        private final TreeSet<MarkerCandidate> candidates;
        private final Map<String, MarkerCandidate> candidatesByPath = new LinkedHashMap<>();

        private BoundedMarkerLane(
                MarkerLaneKey key,
                Comparator<MarkerCandidate> order) {
            this.key = key;
            this.order = order;
            candidates = new TreeSet<>(order);
        }

        private boolean offer(MarkerCandidate candidate, int limit) {
            MarkerCandidate existing = candidatesByPath.get(candidate.path());
            if (existing != null) {
                if (order.compare(candidate, existing) >= 0) return false;
                candidates.remove(existing);
            }
            candidates.add(candidate);
            candidatesByPath.put(candidate.path(), candidate);
            if (candidates.size() <= limit) return false;
            MarkerCandidate removed = candidates.pollLast();
            if (removed != null) candidatesByPath.remove(removed.path());
            return true;
        }

        private MarkerLaneKey key() {
            return key;
        }

        private List<MarkerCandidate> candidates() {
            return List.copyOf(candidates);
        }
    }

    private static final class MarkerLaneCursor {
        private final MarkerLaneKey key;
        private final List<MarkerCandidate> candidates;
        private int offset;

        private MarkerLaneCursor(BoundedMarkerLane lane) {
            key = lane.key();
            candidates = lane.candidates();
        }

        private MarkerLaneKey key() {
            return key;
        }

        private MarkerCandidate first() {
            return candidates.get(0);
        }

        private String next(Set<String> selected) {
            while (offset < candidates.size()) {
                String path = candidates.get(offset++).path();
                if (!selected.contains(path)) return path;
            }
            return null;
        }
    }

    private static final class PluginLaneCursor {
        private final String pluginId;
        private final List<MarkerLaneCursor> lanes;
        private int laneOffset;

        private PluginLaneCursor(
                String pluginId,
                List<MarkerLaneCursor> lanes) {
            this.pluginId = pluginId;
            this.lanes = lanes;
        }

        private String pluginId() {
            return pluginId;
        }

        private MarkerCandidate bestCandidate() {
            return lanes.get(0).first();
        }

        private String next(Set<String> selected) {
            for (int attempts = 0; attempts < lanes.size(); attempts++) {
                MarkerLaneCursor lane = lanes.get(laneOffset);
                laneOffset = (laneOffset + 1) % lanes.size();
                String candidate = lane.next(selected);
                if (candidate != null) return candidate;
            }
            return null;
        }
    }

    public record SelectionPlan(
            String commit,
            List<String> repositoryPaths,
            Map<String, String> markerContents,
            List<ContentPatternMarker> patternMarkers,
            int markerBytes,
            ProjectCapabilities preliminaryCapabilities,
            List<String> enrichmentPaths,
            String projectType,
            String sourceRoot,
            boolean completeRepositoryInventory) {
        public SelectionPlan {
            if (commit == null || commit.isBlank()) {
                throw new IllegalArgumentException("selection commit is required");
            }
            repositoryPaths = List.copyOf(repositoryPaths);
            markerContents = Map.copyOf(markerContents);
            patternMarkers = List.copyOf(patternMarkers);
            enrichmentPaths = List.copyOf(enrichmentPaths);
            if (markerBytes < 0) throw new IllegalArgumentException("marker bytes cannot be negative");
            if (preliminaryCapabilities == null) {
                throw new IllegalArgumentException("preliminary capabilities are required");
            }
        }
    }
}
