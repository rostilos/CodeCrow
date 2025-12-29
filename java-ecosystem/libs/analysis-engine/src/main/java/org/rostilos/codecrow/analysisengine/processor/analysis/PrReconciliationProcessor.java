package org.rostilos.codecrow.analysisengine.processor.analysis;

import okhttp3.OkHttpClient;
import org.rostilos.codecrow.analysisengine.util.VcsInfoHelper;
import org.rostilos.codecrow.analysisengine.util.VcsInfoHelper.VcsInfo;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.branch.BranchIssue;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchIssueRepository;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchRepository;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrReconciliationRequest;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.exception.AnalysisLockedException;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.analysisengine.service.ProjectService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsAiClientService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsOperationsService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsReportingService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.analysisengine.client.AiAnalysisClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.util.*;
import java.util.function.Consumer;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Collectors;

/**
 * Processor for PR reconciliation analysis.
 * 
 * This processor analyzes a PR's changes against existing branch issues to identify
 * issues that could POTENTIALLY be resolved by the PR. Unlike branch analysis,
 * this does NOT modify the branch state - it only posts a comment to the PR
 * indicating which existing issues might be fixed.
 * 
 * The actual resolution status is only updated when the PR is merged (via BranchAnalysisProcessor).
 */
@Service
public class PrReconciliationProcessor {

    private static final Logger log = LoggerFactory.getLogger(PrReconciliationProcessor.class);
    private static final Pattern DIFF_GIT_PATTERN = Pattern.compile("^diff --git\\s+a/(\\S+)\\s+b/(\\S+)");

    private final ProjectService projectService;
    private final BranchRepository branchRepository;
    private final BranchIssueRepository branchIssueRepository;
    private final VcsClientProvider vcsClientProvider;
    private final AiAnalysisClient aiAnalysisClient;
    private final VcsServiceFactory vcsServiceFactory;
    private final AnalysisLockService analysisLockService;
    private final VcsInfoHelper vscInfoHelper;

    public PrReconciliationProcessor(
            ProjectService projectService,
            BranchRepository branchRepository,
            BranchIssueRepository branchIssueRepository,
            VcsClientProvider vcsClientProvider,
            AiAnalysisClient aiAnalysisClient,
            VcsServiceFactory vcsServiceFactory,
            AnalysisLockService analysisLockService,
            VcsInfoHelper vscInfoHelper
    ) {
        this.projectService = projectService;
        this.branchRepository = branchRepository;
        this.branchIssueRepository = branchIssueRepository;
        this.vcsClientProvider = vcsClientProvider;
        this.aiAnalysisClient = aiAnalysisClient;
        this.vcsServiceFactory = vcsServiceFactory;
        this.analysisLockService = analysisLockService;
        this.vscInfoHelper = vscInfoHelper;
    }

    public interface EventConsumer {
        void accept(Map<String, Object> event);
    }

    /**
     * Process PR reconciliation - analyze which existing branch issues might be fixed by this PR.
     * Does NOT modify branch state.
     */
    public Map<String, Object> process(PrReconciliationRequest request, Consumer<Map<String, Object>> consumer) throws IOException {
        Project project = projectService.getProjectWithConnections(request.getProjectId());

        // Use a different lock type to allow running in parallel with PR analysis
        Optional<String> lockKey = analysisLockService.acquireLockWithWait(
                project,
                request.getSourceBranchName(),
                AnalysisLockType.PR_RECONCILIATION,
                request.getCommitHash(),
                request.getPullRequestId(),
                consumer
        );

        if (lockKey.isEmpty()) {
            log.warn("PR reconciliation already in progress for project={}, PR={}",
                    project.getId(), request.getPullRequestId());
            throw new AnalysisLockedException(
                    AnalysisLockType.PR_RECONCILIATION.name(),
                    request.getSourceBranchName(),
                    project.getId()
            );
        }

        try {
            consumer.accept(Map.of(
                    "type", "status",
                    "state", "started",
                    "message", "PR reconciliation started for PR #" + request.getPullRequestId()
            ));

            // Find the target branch
            Optional<Branch> branchOpt = branchRepository.findByProjectIdAndBranchName(
                    project.getId(), request.getTargetBranchName());
            
            if (branchOpt.isEmpty()) {
                log.info("Target branch '{}' not found - no existing issues to check", request.getTargetBranchName());
                return Map.of(
                        "status", "completed",
                        "message", "No existing branch data - reconciliation skipped",
                        "potentiallyResolvedCount", 0
                );
            }

            Branch targetBranch = branchOpt.get();
            
            // Check if there are any unresolved issues on the branch
            long unresolvedCount = branchIssueRepository.countUnresolvedByBranchId(targetBranch.getId());
            if (unresolvedCount == 0) {
                log.info("No unresolved issues on branch '{}' - reconciliation skipped", request.getTargetBranchName());
                consumer.accept(Map.of(
                        "type", "status",
                        "state", "completed",
                        "message", "No existing issues to check"
                ));
                return Map.of(
                        "status", "completed",
                        "message", "No unresolved issues on target branch",
                        "potentiallyResolvedCount", 0
                );
            }

            VcsInfo vcsInfo = vscInfoHelper.getVcsInfo(project);
            OkHttpClient client = vcsClientProvider.getHttpClient(vcsInfo.vcsConnection());

            consumer.accept(Map.of(
                    "type", "status",
                    "state", "fetching_diff",
                    "message", "Fetching PR diff"
            ));

            EVcsProvider provider = vscInfoHelper.getVcsProvider(project);
            VcsOperationsService operationsService = vcsServiceFactory.getOperationsService(provider);

            // Fetch PR diff
            String rawDiff = operationsService.getPullRequestDiff(
                    client,
                    vcsInfo.workspace(),
                    vcsInfo.repoSlug(),
                    String.valueOf(request.getPullRequestId())
            );
            log.info("Fetched PR #{} diff for reconciliation analysis", request.getPullRequestId());

            Set<String> changedFiles = parseFilePathsFromDiff(rawDiff);
            
            if (changedFiles.isEmpty()) {
                log.info("No changed files in PR - reconciliation skipped");
                return Map.of(
                        "status", "completed",
                        "message", "No changed files in PR",
                        "potentiallyResolvedCount", 0
                );
            }

            // Find candidate issues (unresolved issues in files that are being changed)
            List<BranchIssue> candidateIssues = branchIssueRepository.findUnresolvedByBranchIdAndFilePaths(
                    targetBranch.getId(), new ArrayList<>(changedFiles));

            if (candidateIssues.isEmpty()) {
                log.info("No candidate issues found for reconciliation - changed files don't overlap with issue files");
                return Map.of(
                        "status", "completed",
                        "message", "No existing issues in changed files",
                        "potentiallyResolvedCount", 0
                );
            }

            consumer.accept(Map.of(
                    "type", "status",
                    "state", "analyzing_issues",
                    "message", "Analyzing " + candidateIssues.size() + " potentially resolved issues"
            ));

            // Build AI request to analyze if issues are potentially resolved
            List<Map<String, Object>> potentiallyResolved = analyzeIssuesForReconciliation(
                    project, request, candidateIssues, consumer);

            // Post reconciliation comment (not a report)
            if (!potentiallyResolved.isEmpty()) {
                postReconciliationComment(project, request.getPullRequestId(), potentiallyResolved, consumer);
            }

            log.info("PR reconciliation completed: {} potentially resolved issues found", potentiallyResolved.size());

            return Map.of(
                    "status", "completed",
                    "potentiallyResolvedCount", potentiallyResolved.size(),
                    "potentiallyResolvedIssues", potentiallyResolved
            );

        } catch (Exception e) {
            log.error("PR reconciliation failed: {}", e.getMessage(), e);
            consumer.accept(Map.of(
                    "type", "error",
                    "message", "PR reconciliation failed: " + e.getMessage()
            ));
            throw e;
        } finally {
            analysisLockService.releaseLock(lockKey.get());
        }
    }

    private List<Map<String, Object>> analyzeIssuesForReconciliation(
            Project project,
            PrReconciliationRequest request,
            List<BranchIssue> candidateIssues,
            Consumer<Map<String, Object>> consumer
    ) {
        try {
            // Build a temporary CodeAnalysis with the candidate issues
            List<CodeAnalysisIssue> codeAnalysisIssues = candidateIssues.stream()
                    .map(BranchIssue::getCodeAnalysisIssue)
                    .toList();

            CodeAnalysis tempAnalysis = new CodeAnalysis();
            tempAnalysis.setProject(project);
            tempAnalysis.setAnalysisType(AnalysisType.PR_RECONCILIATION);
            tempAnalysis.setPrNumber(request.getPullRequestId());
            tempAnalysis.setCommitHash(request.getCommitHash());
            tempAnalysis.setBranchName(request.getTargetBranchName());
            tempAnalysis.setSourceBranchName(request.getSourceBranchName());
            tempAnalysis.setIssues(codeAnalysisIssues);

            EVcsProvider provider = vscInfoHelper.getVcsProvider(project);
            VcsAiClientService aiClientService = vcsServiceFactory.getAiClientService(provider);

            AiAnalysisRequest aiRequest = aiClientService.buildAiAnalysisRequest(
                    project,
                    request,
                    Optional.of(tempAnalysis)
            );

            Map<String, Object> aiResponse = aiAnalysisClient.performAnalysis(aiRequest, event -> {
                try {
                    consumer.accept(event);
                } catch (Exception ex) {
                    log.warn("Event consumer failed: {}", ex.getMessage());
                }
            });

            // Extract potentially resolved issues from AI response
            List<Map<String, Object>> potentiallyResolved = new ArrayList<>();
            Object issuesObj = aiResponse.get("issues");

            if (issuesObj instanceof List) {
                @SuppressWarnings("unchecked")
                List<Object> issuesList = (List<Object>) issuesObj;
                for (Object item : issuesList) {
                    if (item instanceof Map) {
                        @SuppressWarnings("unchecked")
                        Map<String, Object> issueData = (Map<String, Object>) item;
                        
                        // Check if this issue is marked as resolved/potentially resolved
                        boolean isResolved = false;
                        Object isResolvedObj = issueData.get("isResolved");
                        if (isResolvedObj instanceof Boolean) {
                            isResolved = (Boolean) isResolvedObj;
                        } else if (issueData.get("status") != null) {
                            String status = String.valueOf(issueData.get("status")).toLowerCase();
                            isResolved = status.contains("resolved") || status.contains("fixed");
                        }
                        
                        if (isResolved) {
                            potentiallyResolved.add(issueData);
                        }
                    }
                }
            }

            return potentiallyResolved;

        } catch (Exception e) {
            log.error("Failed to analyze issues for reconciliation: {}", e.getMessage(), e);
            return Collections.emptyList();
        }
    }

    private void postReconciliationComment(
            Project project,
            Long pullRequestId,
            List<Map<String, Object>> potentiallyResolved,
            Consumer<Map<String, Object>> consumer
    ) {
        try {
            EVcsProvider provider = vscInfoHelper.getVcsProvider(project);
            VcsReportingService reportingService = vcsServiceFactory.getReportingService(provider);

            StringBuilder comment = new StringBuilder();
            comment.append("## 🔍 CodeCrow Issue Reconciliation\n\n");
            comment.append("This PR may resolve **").append(potentiallyResolved.size())
                   .append("** existing issue(s) on the target branch:\n\n");

            for (Map<String, Object> issue : potentiallyResolved) {
                String file = String.valueOf(issue.getOrDefault("file", "unknown"));
                String line = String.valueOf(issue.getOrDefault("line", "?"));
                String severity = String.valueOf(issue.getOrDefault("severity", "UNKNOWN"));
                String reason = String.valueOf(issue.getOrDefault("reason", "No description"));
                Object issueId = issue.get("issueId");

                comment.append("- **").append(file).append(":").append(line).append("**");
                if (issueId != null) {
                    comment.append(" (Issue #").append(issueId).append(")");
                }
                comment.append("\n");
                comment.append("  - Severity: `").append(severity).append("`\n");
                comment.append("  - ").append(truncateReason(reason, 200)).append("\n\n");
            }

            comment.append("\n---\n");
            comment.append("*ℹ️ These issues will be marked as resolved when this PR is merged.*\n");
            comment.append("*Note: Final resolution status will be re-verified during merge analysis.*");

            reportingService.postComment(
                    project,
                    pullRequestId,
                    comment.toString(),
                    "codecrow-reconciliation"
            );

            consumer.accept(Map.of(
                    "type", "status",
                    "state", "comment_posted",
                    "message", "Posted reconciliation comment with " + potentiallyResolved.size() + " potentially resolved issues"
            ));

            log.info("Posted reconciliation comment to PR #{}", pullRequestId);

        } catch (Exception e) {
            log.warn("Failed to post reconciliation comment: {}", e.getMessage());
            consumer.accept(Map.of(
                    "type", "warning",
                    "message", "Failed to post reconciliation comment: " + e.getMessage()
            ));
        }
    }

    private String truncateReason(String reason, int maxLength) {
        if (reason == null) return "";
        if (reason.length() <= maxLength) return reason;
        return reason.substring(0, maxLength - 3) + "...";
    }

    private Set<String> parseFilePathsFromDiff(String rawDiff) {
        Set<String> files = new HashSet<>();
        if (rawDiff == null || rawDiff.isBlank()) return files;

        String[] lines = rawDiff.split("\\r?\\n");
        for (String line : lines) {
            Matcher m = DIFF_GIT_PATTERN.matcher(line);
            if (m.find()) {
                String path = m.group(2);
                if (path != null && !path.isBlank()) {
                    files.add(path);
                }
            }
        }
        return files;
    }
}
