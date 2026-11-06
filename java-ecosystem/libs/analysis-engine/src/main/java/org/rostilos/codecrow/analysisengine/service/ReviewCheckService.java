package org.rostilos.codecrow.analysisengine.service;

import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.util.AnalysisScopeFilter;
import org.rostilos.codecrow.analysisengine.util.ProjectVcsInfoRetriever;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.IssueCategory;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.reviewcheck.ReviewCheckFinding;
import org.rostilos.codecrow.core.model.reviewcheck.ReviewCheckResult;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.core.service.reviewcheck.ReviewCheckResultService;
import org.rostilos.codecrow.plugins.CodeCrowPlugin;
import org.rostilos.codecrow.plugins.ReviewCheckPlugin;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.function.Consumer;
import java.util.stream.Collectors;

/** Optional checks own their results; they never read or modify the AI issue payload. */
@Service
public class ReviewCheckService {
    private final VcsClientProvider vcsClients;
    private final ReviewCheckResultService results;
    private final CodeAnalysisService analyses;
    private final List<ReviewCheckPlugin> checks;

    @Autowired
    public ReviewCheckService(VcsClientProvider vcsClients, ReviewCheckResultService results, CodeAnalysisService analyses) {
        this(vcsClients, results, analyses, ServiceLoader.load(CodeCrowPlugin.class).stream()
                .filter(provider -> ReviewCheckPlugin.class.isAssignableFrom(provider.type()))
                .map(provider -> (ReviewCheckPlugin) provider.get()).toList());
    }

    ReviewCheckService(VcsClientProvider vcsClients, ReviewCheckResultService results,
                       CodeAnalysisService analyses, List<ReviewCheckPlugin> checks) {
        this.vcsClients = vcsClients; this.results = results; this.analyses = analyses;
        this.checks = List.copyOf(checks);
    }

    public void run(Project project, AiAnalysisRequest request, CodeAnalysis analysis, Consumer<Map<String, Object>> events) {
        try {
            if (!Objects.equals(project.getId(), analysis.getProject().getId())
                    || !Objects.equals(request.getPullRequestId(), analysis.getPrNumber())) {
                throw new IllegalArgumentException("Review check analysis does not match the requested project and PR");
            }
            if (!results.findForAnalysis(analysis).isEmpty()) return;
            var previous = results.findPrevious(analysis).stream().collect(Collectors.toMap(
                    ReviewCheckResult::getCheckId, result -> result, (first, ignored) -> first, LinkedHashMap::new));
            List<ReviewCheckResult> current = new ArrayList<>();
            if (checks.isEmpty()) diagnostic(events, null, "review-checks", "unavailable: no optional review checks are installed");
            for (var check : checks) {
                String id = check.descriptor().id();
                var prior = previous.remove(id);
                var result = prior == null ? new ReviewCheckResult() : prior.copy(true);
                result.setCheckId(id); result.setComplete(false); result.setDiagnostics(List.of());
                current.add(result);
                if (Boolean.FALSE.equals(project.getEffectiveConfig().reviewChecks().get(id))) {
                    diagnostic(events, result, id, "disabled: project settings disabled this check; last-known findings are retained");
                    continue;
                }
                try {
                    var paths = new ArrayList<>(request.getProposedTreeChangedFiles() == null
                            ? List.<String>of() : request.getProposedTreeChangedFiles());
                    result.getFindings().forEach(finding -> paths.add(finding.getFilePath()));
                    paths.removeIf(path -> !AnalysisScopeFilter.scope(project).includes(path));
                    if (!check.appliesTo(paths)) {
                        if (prior == null) current.remove(result);
                        else if (prior.isComplete() && prior.getFindings().isEmpty()) result.setComplete(true);
                        else diagnostic(events, result, id, "unverified: prior check inputs are outside the current analysis scope");
                        continue;
                    }
                    var contribution = check.check(context(project, request, paths));
                    var known = result.getFindings().stream().collect(Collectors.toMap(ReviewCheckFinding::getFindingKey, finding -> finding));
                    Map<String, ReviewCheckFinding> merged = new LinkedHashMap<>();
                    for (var finding : contribution.findings()) {
                        var entry = new ReviewCheckFinding();
                        entry.setFindingKey(ReviewCheckFinding.key(Objects.requireNonNull(check.findingKey(finding))));
                        entry.setSeverity(IssueSeverity.valueOf(finding.severity())); entry.setIssueCategory(IssueCategory.SECURITY);
                        entry.setFilePath(finding.file()); entry.setLineNumber(finding.line());
                        entry.setTitle(finding.title()); entry.setReason(finding.reason());
                        entry.setSuggestedFixDescription(finding.suggestedFix()); entry.setCodeSnippet(finding.codeSnippet());
                        if (known.containsKey(entry.getFindingKey())) entry.inheritDecision(known.get(entry.getFindingKey()));
                        merged.putIfAbsent(entry.getFindingKey(), entry);
                    }
                    boolean complete = contribution.complete()
                            && known.values().stream().allMatch(finding -> paths.contains(finding.getFilePath()));
                    if (!complete) {
                        known.forEach(merged::putIfAbsent);
                        diagnostic(events, result, id, "incomplete: last-known findings are retained until a complete check succeeds");
                    }
                    result.getFindings().clear(); merged.values().forEach(result::addFinding);
                    result.setComplete(complete);
                    contribution.diagnostics().forEach(message -> diagnostic(events, result, id, message));
                } catch (InterruptedException interrupted) {
                    Thread.currentThread().interrupt();
                    diagnostic(events, result, id, "cancelled: optional review check interrupted");
                    break;
                } catch (Exception unavailable) {
                    diagnostic(events, result, id, "unavailable: check failed; last-known findings are retained and core review continues");
                }
            }
            previous.values().forEach(prior -> {
                var retained = prior.copy(true); retained.setComplete(false); retained.setDiagnostics(List.of());
                diagnostic(events, retained, retained.getCheckId(), "unavailable: check did not run; last-known findings are retained");
                current.add(retained);
            });
            results.save(analysis, current);
            analyses.saveAnalysis(analysis);
        } catch (Exception unavailable) {
            diagnostic(events, null, "review-checks", "unavailable: optional check results could not be loaded or saved; core review continues");
        }
    }

    public void copy(CodeAnalysis source, CodeAnalysis target, Consumer<Map<String, Object>> events) {
        try {
            if (results.findForAnalysis(target).isEmpty()) results.copyForAnalysis(source, target);
            analyses.saveAnalysis(target);
        } catch (Exception unavailable) {
            diagnostic(events, null, "review-checks", "unavailable: cached check results could not be copied; core review continues");
        }
    }

    private ReviewCheckPlugin.Context context(Project project, AiAnalysisRequest request, List<String> paths) throws IOException {
        var repository = ProjectVcsInfoRetriever.getVcsInfo(project);
        var client = vcsClients.getClient(repository.getVcsConnection());
        String target = request.getTargetHeadCommitHash() != null ? request.getTargetHeadCommitHash() : request.getBaseCommitHash();
        String head = request.getCurrentCommitHash();
        String base = target == null || head == null ? null : client.getMergeBase(
                repository.getRepoWorkspace(), repository.getRepoSlug(), target, head);
        return new ReviewCheckPlugin.Context(base, head, paths, (revision, path) -> {
            if (revision == null || (!Objects.equals(revision, base) && !Objects.equals(revision, head))) throw new IOException("Unpinned source revision");
            if (path.startsWith("/") || path.contains("\\")
                    || List.of(path.split("/", -1)).stream().anyMatch(p -> p.isEmpty() || p.equals(".."))) throw new IOException("Invalid source path");
            String content = client.getFileContent(repository.getRepoWorkspace(), repository.getRepoSlug(), path, revision);
            if (content != null && content.getBytes(StandardCharsets.UTF_8).length > 5 * 1024 * 1024) throw new IOException("Check source exceeds size limit");
            return content;
        });
    }

    private static void diagnostic(Consumer<Map<String, Object>> events, ReviewCheckResult result, String id, String message) {
        if (result != null) result.getDiagnostics().add(message);
        events.accept(Map.of("type", "info", "state", "review_check", "plugin", id, "message", message));
    }
}
