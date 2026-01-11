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
    private static final String EMOJI_INFO = "ℹ️";
    private static final String EMOJI_SUCCESS = "✅";
    private static final String EMOJI_WARNING = "⚠️";

    private static final java.util.Map<String, String> CATEGORY_EMOJIS = java.util.Map.ofEntries(
            java.util.Map.entry("SECURITY", "🔒"),
            java.util.Map.entry("PERFORMANCE", "⚡"),
            java.util.Map.entry("CODE_QUALITY", "🧹"),
            java.util.Map.entry("BUG_RISK", "🐛"),
            java.util.Map.entry("STYLE", "🎨"),
            java.util.Map.entry("DOCUMENTATION", "📝"),
            java.util.Map.entry("BEST_PRACTICES", "✨"),
            java.util.Map.entry("ERROR_HANDLING", "🛡️"),
            java.util.Map.entry("TESTING", "🧪"),
            java.util.Map.entry("ARCHITECTURE", "🏗️")
    );

    private final boolean useGitHubSpoilers;

    /**
     * Default constructor - no spoilers (for Bitbucket)
     */
    public MarkdownAnalysisFormatter() {
        this.useGitHubSpoilers = false;
    }

    /**
     * Constructor with spoiler option
     * @param useGitHubSpoilers true for GitHub (uses details/summary), false for Bitbucket
     */
    public MarkdownAnalysisFormatter(boolean useGitHubSpoilers) {
        this.useGitHubSpoilers = useGitHubSpoilers;
    }

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

            if (summary.getInfoSeverityIssues() != null && summary.getInfoSeverityIssues().getCount() > 0) {
                md.append(String.format("| %s Info | [%d](%s) | Informational notes and suggestions |\n",
                        EMOJI_INFO,
                        summary.getInfoSeverityIssues().getCount(),
                        summary.getInfoSeverityIssues().getUrl()));
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
            appendIssuesBySevertiy(md, summary.getIssues(), IssueSeverity.INFO, EMOJI_INFO, "Informational Notes");
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

            if (issue.getCategory() != null && !issue.getCategory().trim().isEmpty()) {
                String categoryEmoji = CATEGORY_EMOJIS.getOrDefault(issue.getCategory(), "📋");
                md.append(String.format("**Category**: %s %s\n\n", categoryEmoji, formatCategory(issue.getCategory())));
            }

            md.append(String.format("**File**: %s\n\n",
                    issue.getLocationDescription()));

            md.append(String.format("**Issue:** %s\n\n", issue.getReason()));

            // Suggested Fix - with spoiler for GitHub, or quote for Bitbucket
            boolean hasSuggestedFix = issue.getSuggestedFix() != null && !issue.getSuggestedFix().trim().isEmpty();
            boolean hasSuggestedDiff = issue.getSuggestedFixDiff() != null && !issue.getSuggestedFixDiff().trim().isEmpty();

            if (hasSuggestedFix || hasSuggestedDiff) {
                if (useGitHubSpoilers) {
                    // GitHub: use details/summary for collapsible sections
                    md.append("<details>\n");
                    md.append("<summary>💡 <b>Suggested Fix</b></summary>\n\n");

                    if (hasSuggestedFix) {
                        md.append(issue.getSuggestedFix().trim());
                        md.append("\n\n");
                    }

                    if (hasSuggestedDiff) {
                        md.append("```diff\n");
                        md.append(issue.getSuggestedFixDiff().trim());
                        md.append("\n```\n\n");
                    }

                    md.append("</details>\n\n");
                } else {
                    // Bitbucket: use quote block
                    if (hasSuggestedFix) {
                        md.append("**Suggested Fix:**\n");
                        String quotedFix = Arrays.stream(issue.getSuggestedFix().trim().split("\n"))
                                .map(line -> "> " + line)
                                .collect(Collectors.joining("\n"));
                        md.append(quotedFix);
                        md.append("\n\n");
                    }

                    if (hasSuggestedDiff) {
                        md.append("**Suggested Code Change:**\n");
                        md.append("```diff\n");
                        md.append(issue.getSuggestedFixDiff().trim());
                        md.append("\n```\n\n");
                    }
                }
            }

            if (issue.getIssueUrl() != null) {
                md.append(String.format("[View Issue Details](%s)\n\n", issue.getIssueUrl()));
            }
            md.append("---\n");
            md.append("\n\n");
        }
    }

    /**
     * Format category name for display (e.g., "CODE_QUALITY" -> "Code Quality")
     */
    private String formatCategory(String category) {
        if (category == null) return "";
        return Arrays.stream(category.split("_"))
                .map(word -> word.charAt(0) + word.substring(1).toLowerCase())
                .collect(Collectors.joining(" "));
    }

    private String getShortFileName(String filePath) {
        if (filePath == null) return "unknown";
        String[] parts = filePath.split("/");
        return parts.length > 2 ? "..." + filePath.substring(filePath.lastIndexOf('/', filePath.lastIndexOf('/') - 1)) : filePath;
    }
}
