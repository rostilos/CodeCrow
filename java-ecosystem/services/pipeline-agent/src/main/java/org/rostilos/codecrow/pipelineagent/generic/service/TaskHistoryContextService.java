package org.rostilos.codecrow.pipelineagent.generic.service;

import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.model.pullrequest.PullRequest;
import org.rostilos.codecrow.core.model.qadoc.QaDocDocument;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository;
import org.rostilos.codecrow.core.persistence.repository.pullrequest.PullRequestRepository;
import org.rostilos.codecrow.core.service.QaDocDocumentService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Service;

import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.Set;
import java.util.function.Function;
import java.util.stream.Collectors;

/**
 * Builds a compact, bounded task history block from server-side records.
 * <p>
 * This intentionally avoids sending historical raw diffs. Prior PR analyses
 * and QA docs are reduced to evidence that helps Stage 2 avoid false
 * task-coverage gaps when a reopened task is completed across multiple PRs.
 */
@Service
public class TaskHistoryContextService {

    private static final Logger log = LoggerFactory.getLogger(TaskHistoryContextService.class);

    private static final int MAX_CONTEXT_CHARS = 7_000;
    private static final int MAX_PRIOR_ANALYSES = 5;
    private static final int MAX_PRIOR_DOCS = 3;
    private static final int MAX_REVIEW_EXCERPT_CHARS = 700;
    private static final int MAX_QA_DOC_EXCERPT_CHARS = 1_200;
    private static final int MAX_FINDINGS_PER_ANALYSIS = 3;

    private final CodeAnalysisRepository codeAnalysisRepository;
    private final PullRequestRepository pullRequestRepository;
    private final QaDocDocumentService qaDocDocumentService;

    public TaskHistoryContextService(CodeAnalysisRepository codeAnalysisRepository,
                                     PullRequestRepository pullRequestRepository,
                                     QaDocDocumentService qaDocDocumentService) {
        this.codeAnalysisRepository = codeAnalysisRepository;
        this.pullRequestRepository = pullRequestRepository;
        this.qaDocDocumentService = qaDocDocumentService;
    }

    public String buildTaskHistoryContext(Long projectId,
                                          Long currentPrNumber,
                                          Map<String, String> taskContext) {
        return buildTaskHistoryContext(projectId, currentPrNumber, taskContext, null);
    }

    public String buildTaskHistoryContext(Long projectId,
                                          Long currentPrNumber,
                                          Map<String, String> taskContext,
                                          String fallbackTaskId) {
        String taskId = firstNonBlankOrNull(
                contextValue(taskContext, "task_key", "taskKey", "key"),
                fallbackTaskId);
        if (projectId == null || taskId == null) {
            return "";
        }

        try {
            List<CodeAnalysis> priorAnalyses = codeAnalysisRepository
                    .findLatestPrAnalysesByProjectIdAndTaskId(
                            projectId,
                            taskId,
                            currentPrNumber,
                            PageRequest.of(0, MAX_PRIOR_ANALYSES));

            List<QaDocDocument> priorDocs = qaDocDocumentService
                    .findDocumentsForTask(projectId, taskId, currentPrNumber, MAX_PRIOR_DOCS);

            if (priorAnalyses.isEmpty() && priorDocs.isEmpty()) {
                return "";
            }

            Map<Long, PullRequest> pullRequests = loadPullRequests(projectId, priorAnalyses, priorDocs);
            StringBuilder sb = new StringBuilder();

            appendLine(sb, "### Prior Task Implementation Context");
            appendLine(sb, "Task: " + taskId + taskSummarySuffix(taskContext));
            appendLine(sb, "Current PR: " + (currentPrNumber != null ? "#" + currentPrNumber : "N/A"));
            appendLine(sb, "History source: persisted CodeCrow PR analyses and QA documents; raw historical diffs are omitted.");

            if (!priorAnalyses.isEmpty()) {
                appendLine(sb, "");
                appendLine(sb, "#### Prior PR Analyses");
                for (CodeAnalysis analysis : priorAnalyses) {
                    appendAnalysis(sb, analysis, pullRequests.get(analysis.getPrNumber()));
                    if (isFull(sb)) {
                        break;
                    }
                }
            }

            if (!priorDocs.isEmpty() && !isFull(sb)) {
                appendLine(sb, "");
                appendLine(sb, "#### Prior QA Documentation Excerpts");
                for (QaDocDocument doc : priorDocs) {
                    appendQaDoc(sb, doc, pullRequests.get(doc.getPrNumber()));
                    if (isFull(sb)) {
                        break;
                    }
                }
            }

            return sb.toString().trim();
        } catch (Exception e) {
            log.warn("Task history context: failed to build context for project {} task {}: {}",
                    projectId, taskId, e.getMessage());
            return "";
        }
    }

    private String firstNonBlankOrNull(String... values) {
        if (values == null) {
            return null;
        }
        for (String value : values) {
            if (value != null && !value.isBlank()) {
                return value.trim();
            }
        }
        return null;
    }

    private Map<Long, PullRequest> loadPullRequests(Long projectId,
                                                    List<CodeAnalysis> analyses,
                                                    List<QaDocDocument> docs) {
        Set<Long> prNumbers = new LinkedHashSet<>();
        analyses.stream().map(CodeAnalysis::getPrNumber).filter(Objects::nonNull).forEach(prNumbers::add);
        docs.stream().map(QaDocDocument::getPrNumber).filter(Objects::nonNull).forEach(prNumbers::add);
        if (prNumbers.isEmpty()) {
            return Map.of();
        }
        return pullRequestRepository
                .findByProject_IdAndPrNumberIn(projectId, List.copyOf(prNumbers))
                .stream()
                .collect(Collectors.toMap(
                        PullRequest::getPrNumber,
                        Function.identity(),
                        (left, right) -> left,
                        LinkedHashMap::new));
    }

    private void appendAnalysis(StringBuilder sb, CodeAnalysis analysis, PullRequest pullRequest) {
        String state = pullRequest != null && pullRequest.getState() != null
                ? pullRequest.getState().name()
                : "UNKNOWN";
        appendLine(sb, "- PR #" + analysis.getPrNumber() + " (" + state + ")");
        appendLine(sb, "  Branch: " + safeBranch(analysis.getSourceBranchName())
                + " -> " + safeBranch(analysis.getBranchName()));
        appendLine(sb, "  Commit: " + shortHash(analysis.getCommitHash())
                + " | Analysis: " + nullToText(analysis.getAnalysisResult()));
        appendLine(sb, "  Active findings: " + activeIssueCount(analysis)
                + " | Resolved findings: " + analysis.getResolvedCount());

        String comment = cleanText(analysis.getComment());
        if (comment != null) {
            appendLine(sb, "  Review summary excerpt: "
                    + truncate(comment, MAX_REVIEW_EXCERPT_CHARS));
        }

        List<CodeAnalysisIssue> notableIssues = notableIssues(analysis);
        if (!notableIssues.isEmpty()) {
            appendLine(sb, "  Notable active findings:");
            for (CodeAnalysisIssue issue : notableIssues) {
                appendLine(sb, "  - [" + nullToText(issue.getSeverity()) + "] "
                        + truncate(firstNonBlank(issue.getTitle(), issue.getReason()), 240));
            }
        }
    }

    private void appendQaDoc(StringBuilder sb, QaDocDocument doc, PullRequest pullRequest) {
        String state = pullRequest != null && pullRequest.getState() != null
                ? pullRequest.getState().name()
                : "UNKNOWN";
        appendLine(sb, "- PR #" + doc.getPrNumber() + " (" + state + "), generated "
                + nullToText(doc.getGeneratedAt()));
        String content = cleanText(stripCodeCrowMarker(doc.getMarkdownContent()));
        if (content != null) {
            appendLine(sb, "  QA coverage excerpt: "
                    + truncate(content, MAX_QA_DOC_EXCERPT_CHARS));
        }
    }

    private List<CodeAnalysisIssue> notableIssues(CodeAnalysis analysis) {
        if (analysis.getIssues() == null) {
            return List.of();
        }
        return analysis.getIssues()
                .stream()
                .filter(issue -> !issue.isResolved())
                .sorted(Comparator.comparingInt(issue -> severityRank(issue.getSeverity())))
                .limit(MAX_FINDINGS_PER_ANALYSIS)
                .toList();
    }

    private int activeIssueCount(CodeAnalysis analysis) {
        if (analysis.getIssues() == null) {
            return analysis.getTotalIssues();
        }
        return (int) analysis.getIssues().stream().filter(issue -> !issue.isResolved()).count();
    }

    private int severityRank(IssueSeverity severity) {
        return severity == null ? Integer.MAX_VALUE : severity.ordinal();
    }

    private String taskSummarySuffix(Map<String, String> taskContext) {
        String summary = contextValue(taskContext, "task_summary", "taskSummary", "summary");
        return summary == null ? "" : " - " + summary;
    }

    private String contextValue(Map<String, String> taskContext, String... keys) {
        if (taskContext == null || taskContext.isEmpty()) {
            return null;
        }
        for (String key : keys) {
            String value = taskContext.get(key);
            if (value != null && !value.isBlank()) {
                return value.trim();
            }
        }
        return null;
    }

    private String stripCodeCrowMarker(String text) {
        if (text == null) {
            return null;
        }
        return text
                .replaceAll("(?s)<!--\\s*codecrow-qa-autodoc[^>]*-->", "")
                .replaceAll("(?m)^\\*.*Generated by \\[CodeCrow\\]\\(https://codecrow\\.app\\) QA Auto-Documentation\\*\\s*", "");
    }

    private String cleanText(String text) {
        if (text == null || text.isBlank()) {
            return null;
        }
        return text.replace('\u0000', ' ')
                .replaceAll("[\\t\\x0B\\f\\r ]+", " ")
                .replaceAll("\\n{3,}", "\n\n")
                .trim();
    }

    private String firstNonBlank(String first, String second) {
        if (first != null && !first.isBlank()) {
            return first;
        }
        if (second != null && !second.isBlank()) {
            return second;
        }
        return "No issue title available";
    }

    private String shortHash(String hash) {
        if (hash == null || hash.isBlank()) {
            return "N/A";
        }
        return hash.length() <= 8 ? hash : hash.substring(0, 8);
    }

    private String safeBranch(String branch) {
        return branch == null || branch.isBlank() ? "N/A" : branch;
    }

    private String nullToText(Object value) {
        return Optional.ofNullable(value).map(String::valueOf).orElse("N/A");
    }

    private String truncate(String value, int maxChars) {
        if (value == null || value.length() <= maxChars) {
            return value;
        }
        return value.substring(0, Math.max(0, maxChars - 3)) + "...";
    }

    private void appendLine(StringBuilder sb, String line) {
        if (sb.length() >= MAX_CONTEXT_CHARS) {
            return;
        }
        String value = line == null ? "" : line;
        int required = value.length() + 1;
        if (sb.length() + required <= MAX_CONTEXT_CHARS) {
            sb.append(value).append('\n');
            return;
        }
        int remaining = MAX_CONTEXT_CHARS - sb.length();
        if (remaining > 20) {
            sb.append(value, 0, remaining - 4).append("...\n");
        }
    }

    private boolean isFull(StringBuilder sb) {
        return sb.length() >= MAX_CONTEXT_CHARS;
    }
}
