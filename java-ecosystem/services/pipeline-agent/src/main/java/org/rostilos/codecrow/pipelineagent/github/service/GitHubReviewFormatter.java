package org.rostilos.codecrow.pipelineagent.github.service;

import org.rostilos.codecrow.core.util.tracking.DiffSanitizer;
import org.rostilos.codecrow.vcsclient.bitbucket.model.report.AnalysisSummary;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * Formats CodeCrow issues for GitHub's pull-request review API.
 */
final class GitHubReviewFormatter {
    private static final int MAX_INLINE_COMMENTS = 20;

    List<Map<String, Object>> formatComments(
            List<AnalysisSummary.IssueSummary> issues,
            String marker
    ) {
        if (issues == null || issues.isEmpty()) {
            return List.of();
        }

        List<Map<String, Object>> comments = new ArrayList<>();
        for (AnalysisSummary.IssueSummary issue : issues) {
            if (comments.size() >= MAX_INLINE_COMMENTS) {
                break;
            }

            String path = normalizePath(issue.getFilePath());
            Integer line = issue.getLineNumber();
            if (path == null || line == null || line <= 0 || !hasConfidentAnchor(issue)) {
                continue;
            }

            Map<String, Object> comment = new LinkedHashMap<>();
            comment.put("path", path);
            comment.put("line", line);
            comment.put("side", "RIGHT");
            comment.put("body", formatBody(issue, marker));
            comments.add(comment);
        }
        return List.copyOf(comments);
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
}
