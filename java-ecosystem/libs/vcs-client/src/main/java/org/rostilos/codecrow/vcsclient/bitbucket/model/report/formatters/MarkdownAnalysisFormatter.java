package org.rostilos.codecrow.vcsclient.bitbucket.model.report.formatters;

import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.vcsclient.bitbucket.model.report.AnalysisSummary;

import java.time.format.DateTimeFormatter;
import java.util.Arrays;
import java.util.List;
import java.util.stream.Collectors;

public class MarkdownAnalysisFormatter implements AnalysisFormatter {
    private static final String EMOJI_HIGH = "🔴";
    private static final String EMOJI_MEDIUM = "🟡";
    private static final String EMOJI_LOW = "🔵";
    private static final String EMOJI_SUCCESS = "✅";
    private static final String EMOJI_WARNING = "⚠️";

    @Override
    public String format(AnalysisSummary summary) {
        StringBuilder md = new StringBuilder();

        if (summary.getTotalIssues() == 0) {
            md.append(String.format("## %s Code Analysis - No Issues Found\n\n", EMOJI_SUCCESS));
        } else {
            md.append(String.format("## %s Code Analysis Results\n\n", EMOJI_WARNING));
        }

        if (summary.getComment() != null && !summary.getComment().trim().isEmpty()) {
            md.append("### Summary\n");
            md.append(summary.getComment()).append("\n\n");
        }

        if (summary.getTotalIssues() > 0) {
            md.append("### Issues Overview\n");
            md.append("| Severity | Count | |\n");
            md.append("|----------|-------|---|\n");

            if (summary.getHighSeverityIssues().getCount() > 0) {
                md.append(String.format("| %s High | [%d](%s) | Critical issues requiring immediate attention |\n",
                        EMOJI_HIGH,
                        summary.getHighSeverityIssues().getCount(),
                        summary.getHighSeverityIssues().getUrl()));
            }

            if (summary.getMediumSeverityIssues().getCount() > 0) {
                md.append(String.format("| %s Medium | [%d](%s) | Issues that should be addressed |\n",
                        EMOJI_MEDIUM,
                        summary.getMediumSeverityIssues().getCount(),
                        summary.getMediumSeverityIssues().getUrl()));
            }

            if (summary.getLowSeverityIssues().getCount() > 0) {
                md.append(String.format("| %s Low | [%d](%s) | Minor issues and improvements |\n",
                        EMOJI_LOW,
                        summary.getLowSeverityIssues().getCount(),
                        summary.getLowSeverityIssues().getUrl()));
            }

            if (summary.getResolvedIssues().getCount() > 0) {
                md.append(String.format("| %s Resolved | [%d](%s) | Resolved issues|\n",
                        EMOJI_SUCCESS,
                        summary.getResolvedIssues().getCount(),
                        summary.getResolvedIssues().getUrl()
                ));
            }

            md.append("\n");

            md.append("### Detailed Issues\n\n");

            appendIssuesBySevertiy(md, summary.getIssues(), IssueSeverity.HIGH, EMOJI_HIGH, "High Severity Issues");
            appendIssuesBySevertiy(md, summary.getIssues(), IssueSeverity.MEDIUM, EMOJI_MEDIUM, "Medium Severity Issues");
            appendIssuesBySevertiy(md, summary.getIssues(), IssueSeverity.LOW, EMOJI_LOW, "Low Severity Issues");
        }

        if (!summary.getFileIssueCount().isEmpty()) {
            md.append("### Files Affected\n");
            summary.getFileIssueCount().entrySet().stream()
                    .sorted((a, b) -> b.getValue().compareTo(a.getValue()))
                    .limit(10)
                    .forEach(entry -> {
                        String fileName = getShortFileName(entry.getKey());
                        md.append(String.format("- **%s**: %d issue%s\n",
                                fileName,
                                entry.getValue(),
                                entry.getValue() == 1 ? "" : "s"));
                    });
            md.append("\n");
        }

        md.append("---\n");
        if (summary.getAnalysisDate() != null) {
            md.append(String.format("*Analysis completed on %s*",
                    summary.getAnalysisDate().format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss"))));
        }

        if (summary.getPlatformAnalysisUrl() != null) {
            md.append(String.format(" | [View Full Report](%s)", summary.getPlatformAnalysisUrl()));
        }

        if (summary.getPullRequestUrl() != null) {
            md.append(String.format(" | [Pull Request](%s)", summary.getPullRequestUrl()));
        }

        return md.toString();
    }

    private void appendIssuesBySevertiy(StringBuilder md, List<AnalysisSummary.IssueSummary> issues,
                                        IssueSeverity severity, String emoji, String title) {
        List<AnalysisSummary.IssueSummary> severityIssues = issues.stream()
                .filter(issue -> issue.getSeverity() == severity)
                .toList();

        if (severityIssues.isEmpty()) {
            return;
        }

        md.append(String.format("### %s %s\n\n", emoji, title));

        for (AnalysisSummary.IssueSummary issue : severityIssues) {
            md.append("\n");

            md.append(String.format("**Id on Platform**: %s\n\n",
                    issue.getIssueId()));

            md.append(String.format("**File**: %s\n\n",
                    issue.getLocationDescription()));

            md.append(String.format("**Issue:** %s\n\n", issue.getReason()));

            if (issue.getSuggestedFix() != null && !issue.getSuggestedFix().trim().isEmpty()) {
                md.append("**Suggested Fix:**\n");

                String quotedFix = Arrays.stream(issue.getSuggestedFix().trim().split("\n"))
                        .map(line -> "> " + line)
                        .collect(Collectors.joining("\n"));

                md.append(quotedFix);
                md.append("\n\n");
            }

            if (issue.getIssueUrl() != null) {
                md.append(String.format("[View Issue Details](%s)\n\n", issue.getIssueUrl()));
            }
            md.append("---\n");
            md.append("\n\n");
        }
    }

    private String getShortFileName(String filePath) {
        if (filePath == null) return "unknown";
        String[] parts = filePath.split("/");
        return parts.length > 2 ? "..." + filePath.substring(filePath.lastIndexOf('/', filePath.lastIndexOf('/') - 1)) : filePath;
    }

    private String truncateText(String text, int maxLength) {
        if (text == null || text.length() <= maxLength) {
            return text;
        }
        return text.substring(0, maxLength - 3) + "...";
    }
}
