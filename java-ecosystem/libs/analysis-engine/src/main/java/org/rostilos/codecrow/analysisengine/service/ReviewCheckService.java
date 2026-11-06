package org.rostilos.codecrow.analysisengine.service;

import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.util.ProjectVcsInfoRetriever;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.plugins.CodeCrowPlugin;
import org.rostilos.codecrow.plugins.ReviewCheckPlugin;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.ServiceLoader;
import java.util.function.Consumer;

/** Hosts optional checks without depending on any concrete implementation. */
@Service
public class ReviewCheckService {
    private final VcsClientProvider vcsClients;
    private final List<ReviewCheckPlugin> checks;

    @Autowired
    public ReviewCheckService(VcsClientProvider vcsClients) {
        this(vcsClients, ServiceLoader.load(CodeCrowPlugin.class).stream()
                .filter(provider -> ReviewCheckPlugin.class.isAssignableFrom(provider.type()))
                .map(provider -> (ReviewCheckPlugin) provider.get()).toList());
    }

    ReviewCheckService(VcsClientProvider vcsClients, List<ReviewCheckPlugin> checks) {
        this.vcsClients = vcsClients;
        this.checks = List.copyOf(checks);
    }

    public Map<String, Object> enrich(Project project, AiAnalysisRequest request,
                                     Map<String, Object> response, Consumer<Map<String, Object>> events) {
        Map<String, Object> result = new LinkedHashMap<>(response);
        List<Object> issues = new ArrayList<>();
        if (response.get("issues") instanceof List<?> list) issues.addAll(list);
        else if (response.get("issues") instanceof Map<?, ?> map) issues.addAll(map.values());
        // Only the host assigns check provenance; model output cannot opt out of inline review.
        issues.replaceAll(value -> {
            if (!(value instanceof Map<?, ?> issue)) return value;
            var copy = new LinkedHashMap<>(issue);
            copy.remove("reviewCheckId");
            return copy;
        });
        List<Map<String, Object>> diagnostics = new ArrayList<>();
        if (checks.isEmpty()) {
            diagnostic(events, diagnostics, "review-checks", "unavailable: no optional review checks are installed");
        }
        for (ReviewCheckPlugin check : checks) {
            String id = check.descriptor().id();
            if (Boolean.FALSE.equals(project.getEffectiveConfig().reviewChecks().get(id))) {
                diagnostic(events, diagnostics, id, "disabled: project settings disabled this check");
                continue;
            }
            try {
                var paths = request.getProposedTreeChangedFiles() == null ? List.<String>of() : request.getProposedTreeChangedFiles().stream()
                        .filter(org.rostilos.codecrow.analysisengine.util.AnalysisScopeFilter.scope(project)::includes).toList();
                if (!check.appliesTo(paths)) continue;
                var repository = ProjectVcsInfoRetriever.getVcsInfo(project);
                var client = vcsClients.getClient(repository.getVcsConnection());
                String target = request.getTargetHeadCommitHash() != null ? request.getTargetHeadCommitHash() : request.getBaseCommitHash();
                String head = request.getCurrentCommitHash();
                String base = target == null || head == null ? null : client.getMergeBase(
                        repository.getRepoWorkspace(), repository.getRepoSlug(), target, head);
                var context = new ReviewCheckPlugin.Context(base, head, paths,
                        (revision, path) -> {
                            if (!revision.equals(base) && !revision.equals(head)) {
                                throw new IOException("Check attempted to read an unpinned revision");
                            }
                            if (path.startsWith("/") || path.contains("\\")
                                    || List.of(path.split("/", -1)).stream().anyMatch(p -> p.isEmpty() || p.equals(".."))) {
                                throw new IOException("Invalid source path");
                            }
                            String content = client.getFileContent(repository.getRepoWorkspace(),
                                    repository.getRepoSlug(), path, revision);
                            if (content != null && content.getBytes(StandardCharsets.UTF_8).length > 5 * 1024 * 1024) {
                                throw new IOException("Check input exceeds the source size limit");
                            }
                            return content;
                        });
                var contribution = check.check(context);
                for (var finding : contribution.findings()) {
                    Map<String, Object> issue = new LinkedHashMap<>();
                    issue.put("file", finding.file());
                    issue.put("line", finding.line());
                    issue.put("severity", finding.severity());
                    issue.put("category", "SECURITY");
                    issue.put("reviewCheckId", id);
                    issue.put("title", finding.title());
                    issue.put("reason", finding.reason());
                    issue.put("suggestedFixDescription", finding.suggestedFix());
                    issue.put("codeSnippet", finding.codeSnippet());
                    issues.add(issue);
                }
                contribution.diagnostics().forEach(message -> diagnostic(events, diagnostics, id, message));
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
                diagnostic(events, diagnostics, id, "cancelled: optional review check interrupted");
                break;
            } catch (Exception unavailable) {
                diagnostic(events, diagnostics, id, "unavailable: optional review check failed; core review continues");
            }
        }
        result.put("issues", issues);
        result.put("reviewChecks", diagnostics);
        return result;
    }

    private static void diagnostic(Consumer<Map<String, Object>> events, List<Map<String, Object>> diagnostics,
                                   String plugin, String message) {
        var event = Map.<String, Object>of("type", "info", "state", "review_check", "plugin", plugin, "message", message);
        diagnostics.add(event);
        events.accept(event);
    }
}
