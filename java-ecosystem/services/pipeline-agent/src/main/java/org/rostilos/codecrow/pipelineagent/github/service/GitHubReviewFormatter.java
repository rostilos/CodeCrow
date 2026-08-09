package org.rostilos.codecrow.pipelineagent.github.service;

import org.rostilos.codecrow.core.util.tracking.DiffSanitizer;
import org.rostilos.codecrow.vcsclient.bitbucket.model.report.AnalysisSummary;
import org.rostilos.codecrow.vcsclient.github.actions.GetPullRequestDiffAction.PullRequestFilePatch;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Formats CodeCrow issues for GitHub's pull-request review API.
 */
final class GitHubReviewFormatter {
    private static final int MAX_INLINE_COMMENTS = 20;
    private static final Pattern HUNK_HEADER = Pattern.compile(
            "^@@ -\\d+(?:,\\d+)? \\+(\\d+)(?:,\\d+)? @@.*$");
    private static final String OUTSIDE_DIFF_REASON =
            "The reported line is outside the current pull-request diff.";
    private static final String DIFF_UNAVAILABLE_REASON =
            "The current pull-request diff could not be loaded, so CodeCrow did not guess an inline anchor.";

    ReviewPlan planComments(
            List<AnalysisSummary.IssueSummary> issues,
            String marker,
            List<PullRequestFilePatch> filePatches
    ) {
        if (issues == null || issues.isEmpty()) {
            return ReviewPlan.empty();
        }

        Map<String, Set<Integer>> rightSideLines = collectRightSideLines(filePatches);
        List<Map<String, Object>> comments = new ArrayList<>();
        List<NonInlineFinding> nonInlineFindings = new ArrayList<>();
        for (AnalysisSummary.IssueSummary issue : issues) {
            String path = normalizePath(issue.getFilePath());
            Integer line = issue.getLineNumber();
            String ineligibleReason = ineligibleReason(issue, path, line);
            if (ineligibleReason != null) {
                nonInlineFindings.add(new NonInlineFinding(issue, ineligibleReason));
                continue;
            }
            if (!rightSideLines.getOrDefault(path, Set.of()).contains(line)) {
                nonInlineFindings.add(new NonInlineFinding(issue, OUTSIDE_DIFF_REASON));
                continue;
            }
            if (comments.size() >= MAX_INLINE_COMMENTS) {
                nonInlineFindings.add(new NonInlineFinding(
                        issue,
                        "GitHub's limit of " + MAX_INLINE_COMMENTS
                                + " CodeCrow inline comments was reached."));
                continue;
            }

            Map<String, Object> comment = new LinkedHashMap<>();
            comment.put("path", path);
            comment.put("line", line);
            comment.put("side", "RIGHT");
            comment.put("body", formatBody(issue, marker));
            comments.add(comment);
        }
        return new ReviewPlan(comments, nonInlineFindings);
    }

    ReviewPlan planWithoutDiff(List<AnalysisSummary.IssueSummary> issues) {
        if (issues == null || issues.isEmpty()) {
            return ReviewPlan.empty();
        }
        return new ReviewPlan(
                List.of(),
                issues.stream()
                        .map(issue -> new NonInlineFinding(issue, DIFF_UNAVAILABLE_REASON))
                        .toList());
    }

    String formatNonInlineFindings(List<NonInlineFinding> findings) {
        if (findings == null || findings.isEmpty()) {
            return "";
        }

        StringBuilder markdown = new StringBuilder();
        markdown.append("<details>\n<summary><b>📍 Findings not posted inline (")
                .append(findings.size())
                .append(")</b></summary>\n\n")
                .append("GitHub only accepts inline review comments on lines available in the current pull-request diff. ")
                .append("These findings remain part of the complete review.\n\n");

        for (NonInlineFinding finding : findings) {
            AnalysisSummary.IssueSummary issue = finding.issue();
            String title = issue.getTitle() == null || issue.getTitle().isBlank()
                    ? "Untitled finding"
                    : issue.getTitle();
            markdown.append("- ").append(severityEmoji(issue)).append(" **")
                    .append(issue.getSeverity()).append("** — ");
            if (issue.getIssueUrl() != null && !issue.getIssueUrl().isBlank()) {
                markdown.append("[").append(title).append("](")
                        .append(issue.getIssueUrl()).append(")");
            } else {
                markdown.append("**").append(title).append("**");
            }
            markdown.append(" at `")
                    .append(issue.getLocationDescription().replace("`", "\\`"))
                    .append("`\n  - ").append(finding.reason()).append("\n");
        }

        markdown.append("\n</details>");
        return markdown.toString();
    }

    String formatReviewBody(int commentCount, String marker) {
        return "## CodeCrow Review\n\n"
                + "**Actionable comments posted: " + commentCount + "**\n\n"
                + "Each finding below is attached to the relevant changed line. "
                + "The complete analysis remains available in the CodeCrow summary comment.\n\n"
                + marker;
    }

    private boolean hasConfidentAnchor(AnalysisSummary.IssueSummary issue) {
        return issue.getLineNumber() > 1
                || (issue.getCodeSnippet() != null && !issue.getCodeSnippet().isBlank());
    }

    private String ineligibleReason(
            AnalysisSummary.IssueSummary issue,
            String path,
            Integer line
    ) {
        if (path == null) {
            return "No repository file path was available for an inline anchor.";
        }
        if (line == null || line <= 0) {
            return "No positive source line was available for an inline anchor.";
        }
        if (!hasConfidentAnchor(issue)) {
            return "The finding does not have a confident source-line anchor.";
        }
        return null;
    }

    private Map<String, Set<Integer>> collectRightSideLines(
            List<PullRequestFilePatch> filePatches
    ) {
        Map<String, Set<Integer>> rightSideLines = new HashMap<>();
        if (filePatches == null) {
            return rightSideLines;
        }

        for (PullRequestFilePatch filePatch : filePatches) {
            String path = normalizePath(filePatch.filename());
            if (path == null || filePatch.patch().isBlank()) {
                continue;
            }
            Set<Integer> lines = rightSideLines.computeIfAbsent(
                    path, ignored -> new HashSet<>());
            collectRightSideLines(filePatch.patch(), lines);
        }
        return rightSideLines;
    }

    private void collectRightSideLines(String patch, Set<Integer> lines) {
        int nextRightLine = -1;
        boolean inHunk = false;
        for (String diffLine : patch.split("\\r?\\n", -1)) {
            Matcher hunk = HUNK_HEADER.matcher(diffLine);
            if (hunk.matches()) {
                nextRightLine = Integer.parseInt(hunk.group(1));
                inHunk = true;
                continue;
            }
            if (!inHunk || diffLine.isEmpty() || diffLine.startsWith("\\")) {
                continue;
            }

            char prefix = diffLine.charAt(0);
            if (prefix == '+') {
                lines.add(nextRightLine++);
            } else if (prefix == ' ') {
                lines.add(nextRightLine++);
            } else if (prefix != '-') {
                inHunk = false;
            }
        }
    }

    private String normalizePath(String path) {
        if (path == null || path.isBlank()) {
            return null;
        }
        String normalized = path.trim().replace('\\', '/');
        while (normalized.startsWith("/")) {
            normalized = normalized.substring(1);
        }
        return normalized.isBlank() ? null : normalized;
    }

    private String formatBody(AnalysisSummary.IssueSummary issue, String marker) {
        StringBuilder body = new StringBuilder();
        body.append(severityEmoji(issue)).append(" **")
                .append(issue.getSeverity()).append("**");

        if (issue.getCategory() != null && !issue.getCategory().isBlank()) {
            body.append(" | ").append(humanizeCategory(issue.getCategory()));
        }

        if (issue.getTitle() != null && !issue.getTitle().isBlank()) {
            body.append("\n\n**").append(issue.getTitle()).append("**");
        }
        if (issue.getReason() != null && !issue.getReason().isBlank()) {
            body.append("\n\n").append(issue.getReason());
        }

        appendSuggestedFix(body, issue);

        if (issue.getIssueUrl() != null && !issue.getIssueUrl().isBlank()) {
            body.append("\n\n[View issue in CodeCrow](")
                    .append(issue.getIssueUrl()).append(")");
        }

        body.append("\n\n").append(marker);
        return body.toString();
    }

    private void appendSuggestedFix(
            StringBuilder body,
            AnalysisSummary.IssueSummary issue
    ) {
        boolean hasDescription = DiffSanitizer.hasRealFixDescription(issue.getSuggestedFix());
        boolean hasDiff = DiffSanitizer.isValidDiffFormat(issue.getSuggestedFixDiff());
        if (!hasDescription && !hasDiff) {
            return;
        }

        body.append("\n\n<details>\n<summary>💡 Suggested fix</summary>\n\n");
        if (hasDescription) {
            body.append(issue.getSuggestedFix());
        }
        if (hasDiff) {
            if (hasDescription) {
                body.append("\n\n");
            }
            body.append("```diff\n").append(issue.getSuggestedFixDiff()).append("\n```");
        }
        body.append("\n\n</details>");
    }

    private String severityEmoji(AnalysisSummary.IssueSummary issue) {
        if (issue.getSeverity() == null) {
            return "ℹ️";
        }
        return switch (issue.getSeverity()) {
            case HIGH -> "🔴";
            case MEDIUM -> "🟡";
            case LOW -> "🔵";
            default -> "ℹ️";
        };
    }

    private String humanizeCategory(String category) {
        String normalized = category.trim().replace('_', ' ').toLowerCase(Locale.ROOT);
        StringBuilder result = new StringBuilder(normalized.length());
        boolean capitalize = true;
        for (char character : normalized.toCharArray()) {
            if (capitalize && Character.isLetter(character)) {
                result.append(Character.toUpperCase(character));
                capitalize = false;
            } else {
                result.append(character);
            }
            if (character == ' ') {
                capitalize = true;
            }
        }
        return result.toString();
    }

    record ReviewPlan(
            List<Map<String, Object>> inlineComments,
            List<NonInlineFinding> nonInlineFindings
    ) {
        ReviewPlan {
            inlineComments = inlineComments == null ? List.of() : List.copyOf(inlineComments);
            nonInlineFindings = nonInlineFindings == null
                    ? List.of()
                    : List.copyOf(nonInlineFindings);
        }

        static ReviewPlan empty() {
            return new ReviewPlan(List.of(), List.of());
        }
    }

    record NonInlineFinding(AnalysisSummary.IssueSummary issue, String reason) {
    }
}
