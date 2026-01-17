package org.rostilos.codecrow.vcsclient.bitbucket.model.report.formatters;

import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.vcsclient.bitbucket.model.report.AnalysisSummary;

import java.time.format.DateTimeFormatter;
import java.util.List;

public class PlainTextAnalysisFormatter implements AnalysisFormatter {

    @Override
    public String format(AnalysisSummary summary) {
        StringBuilder text = new StringBuilder();

        if (summary.getTotalIssues() == 0) {
            text.append("=== CODE ANALYSIS - NO ISSUES FOUND ===\n\n");
        } else {
            text.append("=== CODE ANALYSIS RESULTS ===\n\n");
        }

        if (summary.getComment() != null && !summary.getComment().trim().isEmpty()) {
            text.append("SUMMARY:\n");
            text.append(summary.getComment()).append("\n\n");
        }

        if (summary.getTotalIssues() > 0) {
            text.append("ISSUES OVERVIEW:\n");
            text.append(String.format("Total Issues: %d\n", summary.getTotalIssues()));

            if (summary.getHighSeverityIssues().getCount() > 0) {
                text.append(String.format("- High Severity: %d (Critical issues requiring immediate attention)\n",
                        summary.getHighSeverityIssues().getCount()));
            }

            if (summary.getMediumSeverityIssues().getCount() > 0) {
                text.append(String.format("- Medium Severity: %d (Issues that should be addressed)\n",
                        summary.getMediumSeverityIssues().getCount()));
            }

            if (summary.getLowSeverityIssues().getCount() > 0) {
                text.append(String.format("- Low Severity: %d (Minor issues and improvements)\n",
                        summary.getLowSeverityIssues().getCount()));
            }

            if (summary.getInfoSeverityIssues() != null && summary.getInfoSeverityIssues().getCount() > 0) {
                text.append(String.format("- Info: %d (Informational notes and suggestions)\n",
                        summary.getInfoSeverityIssues().getCount()));
            }

            text.append("\n");

            text.append("DETAILED ISSUES:\n\n");

            appendIssuesBySevertiy(text, summary.getIssues(), IssueSeverity.HIGH, "HIGH SEVERITY ISSUES");
            appendIssuesBySevertiy(text, summary.getIssues(), IssueSeverity.MEDIUM, "MEDIUM SEVERITY ISSUES");
            appendIssuesBySevertiy(text, summary.getIssues(), IssueSeverity.LOW, "LOW SEVERITY ISSUES");
            appendIssuesBySevertiy(text, summary.getIssues(), IssueSeverity.INFO, "INFORMATIONAL NOTES");
        }

        if (!summary.getFileIssueCount().isEmpty()) {
            text.append("FILES AFFECTED:\n");
            summary.getFileIssueCount().entrySet().stream()
                    .sorted((a, b) -> b.getValue().compareTo(a.getValue()))
                    .limit(10)
                    .forEach(entry -> {
                        String fileName = getShortFileName(entry.getKey());
                        text.append(String.format("- %s: %d issue%s\n",
                                fileName,
                                entry.getValue(),
                                entry.getValue() == 1 ? "" : "s"));
                    });
            text.append("\n");
        }

        text.append("===============================================\n");
        if (summary.getAnalysisDate() != null) {
            text.append(String.format("Analysis completed on: %s\n",
                    summary.getAnalysisDate().format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss"))));
        }

        if (summary.getPlatformAnalysisUrl() != null) {
            text.append(String.format("Full Report: %s\n", summary.getPlatformAnalysisUrl()));
        }

        if (summary.getPullRequestUrl() != null) {
            text.append(String.format("Pull Request: %s\n", summary.getPullRequestUrl()));
        }

        return text.toString();
    }

    private void appendIssuesBySevertiy(StringBuilder text, List<AnalysisSummary.IssueSummary> issues,
                                        IssueSeverity severity, String title) {
        List<AnalysisSummary.IssueSummary> severityIssues = issues.stream()
                .filter(issue -> issue.getSeverity() == severity)
                .toList();

        if (severityIssues.isEmpty()) {
            return;
        }

        text.append(String.format("%s:\n", title));
        text.append("-".repeat(title.length() + 1)).append("\n");

        int count = 1;
        for (AnalysisSummary.IssueSummary issue : severityIssues) {
            text.append(String.format("%d. %s\n", count++, issue.getLocationDescription()));

            // Add category
            if (issue.getCategory() != null && !issue.getCategory().trim().isEmpty()) {
                text.append(String.format("   Category: %s\n", formatCategory(issue.getCategory())));
            }

            text.append(String.format("   Issue: %s\n", issue.getReason()));

            if (issue.getSuggestedFix() != null && !issue.getSuggestedFix().trim().isEmpty()) {
                text.append("   Suggested Fix:\n");
                String[] fixLines = issue.getSuggestedFix().split("\n");
                for (String line : fixLines) {
                    text.append("   ").append(line).append("\n");
                }
            }

            if (issue.getSuggestedFixDiff() != null && !issue.getSuggestedFixDiff().trim().isEmpty()) {
                text.append("   Suggested Code Change:\n");
                String[] diffLines = issue.getSuggestedFixDiff().split("\n");
                for (String line : diffLines) {
                    text.append("   ").append(line).append("\n");
                }
            }

            if (issue.getIssueUrl() != null) {
                text.append(String.format("   Details: %s\n", issue.getIssueUrl()));
            }

            text.append("\n");
        }
    }

    /**
     * Format category name for display (e.g., "CODE_QUALITY" -> "Code Quality")
     */
    private String formatCategory(String category) {
        if (category == null) return "";
        String[] parts = category.split("_");
        StringBuilder result = new StringBuilder();
        for (String part : parts) {
            if (!result.isEmpty()) result.append(" ");
            result.append(part.charAt(0)).append(part.substring(1).toLowerCase());
        }
        return result.toString();
    }

    private String getShortFileName(String filePath) {
        if (filePath == null) return "unknown";
        String[] parts = filePath.split("/");
        return parts.length > 2 ? "..." + filePath.substring(filePath.lastIndexOf('/', filePath.lastIndexOf('/') - 1)) : filePath;
    }
}

