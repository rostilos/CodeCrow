package org.rostilos.codecrow.pipelineagent.bitbucket.service;

import org.rostilos.codecrow.core.util.tracking.DiffSanitizer;
import org.rostilos.codecrow.vcsclient.bitbucket.model.report.AnalysisSummary;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;
import java.util.stream.Collectors;

/**
 * Formats analysis findings as native Bitbucket Cloud inline review comments.
 */
final class BitbucketInlineCommentFormatter {

    List<InlineComment> formatComments(
            List<AnalysisSummary.IssueSummary> issues,
            String marker
    ) {
        if (issues == null || issues.isEmpty()) {
            return List.of();
        }

        List<InlineComment> comments = new ArrayList<>();
        for (AnalysisSummary.IssueSummary issue : issues) {
            String path = normalizePath(issue.getFilePath());
            Integer line = issue.getLineNumber();
            if (path == null || line == null || line <= 0 || !hasConfidentAnchor(issue)) {
                continue;
            }

            comments.add(new InlineComment(path, line, formatBody(issue, marker)));
        }
        return List.copyOf(comments);
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
            body.append("\n\n**").append(issue.getTitle().trim()).append("**");
        }
        if (issue.getReason() != null && !issue.getReason().isBlank()) {
            body.append("\n\n").append(issue.getReason().trim());
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

        if (hasDescription) {
            String quotedFix = Arrays.stream(issue.getSuggestedFix().trim().split("\\R"))
                    .map(line -> "> " + line)
                    .collect(Collectors.joining("\n"));
            body.append("\n\n**Suggested fix**\n\n").append(quotedFix);
        }

        if (hasDiff) {
            body.append("\n\n**Suggested code change**\n\n```diff\n")
                    .append(issue.getSuggestedFixDiff().trim())
                    .append("\n```");
        }
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

    record InlineComment(String path, int line, String body) {
    }
}
