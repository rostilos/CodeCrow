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
import java.util.LinkedHashMap;
import java.util.ArrayList;
import java.util.Collection;
import java.util.List;
import java.util.Map;
import java.util.TreeSet;

@Service
public class ProjectCapabilitySelectionService {
    private static final Logger log = LoggerFactory.getLogger(ProjectCapabilitySelectionService.class);
    private static final int MAX_MARKER_FILES = 16;
    private static final int MAX_MARKER_BYTES = 262_144;

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
        TreeSet<String> paths = new TreeSet<>();
        if (changedFiles != null) {
            changedFiles.stream().map(ProjectCapabilitySelectionService::normalize)
                    .forEach(paths::add);
        }

        String projectType = analysisProfile != null ? analysisProfile.projectType() : null;
        String sourceRoot = analysisProfile != null ? analysisProfile.sourceRoot() : null;
        TreeSet<String> markerPaths = new TreeSet<>();
        TreeSet<ContentPatternMarker> patternMarkers = new TreeSet<>();
        if (projectType == null) {
            for (PluginDescriptor descriptor : registry.descriptors()) {
                markerPaths.addAll(descriptor.detection().filesAll());
                markerPaths.addAll(descriptor.detection().filesAny());
                descriptor.detection().contentMarkers().stream()
                        .map(ContentMarker::path)
                        .forEach(markerPaths::add);
                descriptor.detection().alternatives().forEach(alternative -> {
                    markerPaths.addAll(alternative.filesAll());
                    markerPaths.addAll(alternative.filesAny());
                    alternative.contentMarkers().stream()
                            .map(ContentMarker::path)
                            .forEach(markerPaths::add);
                    patternMarkers.addAll(alternative.contentPatternMarkers());
                });
            }
        }
        TreeSet<String> resolvedMarkerPaths = sourceRoot == null
                ? markerPaths
                : markerPaths.stream()
                        .map(path -> sourceRoot + "/" + path)
                        .collect(java.util.stream.Collectors.toCollection(TreeSet::new));
        if (resolvedMarkerPaths.size() > MAX_MARKER_FILES) {
            throw new IllegalStateException("plugin marker declarations exceed the host budget");
        }

        Map<String, String> markerContents = new LinkedHashMap<>();
        int consumed = 0;
        for (String markerPath : resolvedMarkerPaths) {
            try {
                String content = vcsClient.getFileContent(
                        workspace, repository, markerPath, commit);
                if (content == null) continue;
                int bytes = content.getBytes(java.nio.charset.StandardCharsets.UTF_8).length;
                if (consumed + bytes > MAX_MARKER_BYTES) {
                    throw new IllegalStateException("plugin marker contents exceed the host budget");
                }
                consumed += bytes;
                paths.add(markerPath);
                markerContents.put(markerPath, content);
            } catch (IOException exception) {
                throw new IllegalStateException(
                        "Cannot read plugin marker from the pinned repository snapshot: " + markerPath,
                        exception);
            }
        }

        ProjectCapabilities preliminary = selector.select(
                new RepositoryFacts(
                        commit, List.copyOf(paths), markerContents,
                        projectType, sourceRoot));
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
                sourceRoot);
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
        if (enrichment != null && enrichment.fileContents() != null) {
            enrichment.fileContents().stream()
                    .map(file -> normalize(file.path()))
                    .forEach(paths::add);
            for (ContentPatternMarker marker : plan.patternMarkers()) {
                var matchingFile = enrichment.fileContents().stream()
                        .filter(file -> !file.skipped() && file.content() != null)
                        .filter(file -> {
                            String relative = relativeToRoot(
                                    normalize(file.path()), plan.sourceRoot());
                            return relative != null
                                    && PluginGlob.matches(marker.pathPattern(), relative);
                        })
                        .filter(file -> file.content().contains(marker.contains()))
                        .findFirst();
                if (matchingFile.isEmpty()) continue;
                String path = normalize(matchingFile.get().path());
                if (markerContents.containsKey(path)) continue;
                String content = matchingFile.get().content();
                int bytes = content.getBytes(java.nio.charset.StandardCharsets.UTF_8).length;
                if (consumed + bytes > MAX_MARKER_BYTES) {
                    throw new IllegalStateException("plugin marker contents exceed the host byte budget");
                }
                consumed += bytes;
                markerContents.put(path, content);
            }
        }

        return selector.select(new RepositoryFacts(
                plan.commit(), List.copyOf(paths), markerContents,
                plan.projectType(), plan.sourceRoot()));
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

    public record SelectionPlan(
            String commit,
            List<String> repositoryPaths,
            Map<String, String> markerContents,
            List<ContentPatternMarker> patternMarkers,
            int markerBytes,
            ProjectCapabilities preliminaryCapabilities,
            List<String> enrichmentPaths,
            String projectType,
            String sourceRoot) {
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
