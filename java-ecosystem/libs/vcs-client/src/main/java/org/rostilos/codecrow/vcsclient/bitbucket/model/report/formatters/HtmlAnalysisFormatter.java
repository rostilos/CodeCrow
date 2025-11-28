package org.rostilos.codecrow.vcsclient.bitbucket.model.report.formatters;

import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.vcsclient.bitbucket.model.report.AnalysisSummary;

import java.time.format.DateTimeFormatter;
import java.util.List;

public class HtmlAnalysisFormatter implements AnalysisFormatter {

    @Override
    public String format(AnalysisSummary summary) {
        StringBuilder html = new StringBuilder();

        html.append("<div class=\"analysis-summary\">\n");

        if (summary.getTotalIssues() == 0) {
            html.append("<h2 class=\"success\">✅ Code Analysis - No Issues Found</h2>\n");
        } else {
            html.append("<h2 class=\"warning\">⚠️ Code Analysis Results</h2>\n");
        }

        if (summary.getComment() != null && !summary.getComment().trim().isEmpty()) {
            html.append("<div class=\"summary-comment\">\n");
            html.append("<h3>Summary</h3>\n");
            html.append("<p>").append(escapeHtml(summary.getComment())).append("</p>\n");
            html.append("</div>\n");
        }

        if (summary.getTotalIssues() > 0) {
            html.append("<div class=\"issues-overview\">\n");
            html.append("<h3>Issues Overview</h3>\n");
            html.append("<table class=\"issues-table\">\n");
            html.append("<thead><tr><th>Severity</th><th>Count</th><th>Description</th></tr></thead>\n");
            html.append("<tbody>\n");

            if (summary.getHighSeverityIssues().getCount() > 0) {
                html.append(String.format(
                        "<tr class=\"high-severity\">" +
                                "<td>🔴 High</td>" +
                                "<td><a href=\"%s\">%d</a></td>" +
                                "<td>Critical issues requiring immediate attention</td>" +
                                "</tr>\n",
                        summary.getHighSeverityIssues().getUrl(),
                        summary.getHighSeverityIssues().getCount()));
            }

            if (summary.getMediumSeverityIssues().getCount() > 0) {
                html.append(String.format(
                        "<tr class=\"medium-severity\">" +
                                "<td>🟡 Medium</td>" +
                                "<td><a href=\"%s\">%d</a></td>" +
                                "<td>Issues that should be addressed</td>" +
                                "</tr>\n",
                        summary.getMediumSeverityIssues().getUrl(),
                        summary.getMediumSeverityIssues().getCount()));
            }

            if (summary.getLowSeverityIssues().getCount() > 0) {
                html.append(String.format(
                        "<tr class=\"low-severity\">" +
                                "<td>🔵 Low</td>" +
                                "<td><a href=\"%s\">%d</a></td>" +
                                "<td>Minor issues and improvements</td>" +
                                "</tr>\n",
                        summary.getLowSeverityIssues().getUrl(),
                        summary.getLowSeverityIssues().getCount()));
            }

            html.append("</tbody></table>\n");
            html.append("</div>\n");

            html.append("<div class=\"detailed-issues\">\n");
            html.append("<h3>Detailed Issues</h3>\n");

            appendIssuesBySevertiy(html, summary.getIssues(), IssueSeverity.HIGH, "high", "🔴 High Severity Issues");
            appendIssuesBySevertiy(html, summary.getIssues(), IssueSeverity.MEDIUM, "medium", "🟡 Medium Severity Issues");
            appendIssuesBySevertiy(html, summary.getIssues(), IssueSeverity.LOW, "low", "🔵 Low Severity Issues");

            html.append("</div>\n");
        }

        if (!summary.getFileIssueCount().isEmpty()) {
            html.append("<div class=\"files-affected\">\n");
            html.append("<h3>Files Affected</h3>\n");
            html.append("<ul>\n");
            summary.getFileIssueCount().entrySet().stream()
                    .sorted((a, b) -> b.getValue().compareTo(a.getValue()))
                    .limit(10)
                    .forEach(entry -> {
                        String fileName = getShortFileName(entry.getKey());
                        html.append(String.format("<li><strong>%s</strong>: %d issue%s</li>\n",
                                escapeHtml(fileName),
                                entry.getValue(),
                                entry.getValue() == 1 ? "" : "s"));
                    });
            html.append("</ul>\n");
            html.append("</div>\n");
        }

        html.append("<div class=\"analysis-footer\">\n");
        if (summary.getAnalysisDate() != null) {
            html.append(String.format("<p><em>Analysis completed on %s</em>",
                    summary.getAnalysisDate().format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss"))));
        }

        if (summary.getPlatformAnalysisUrl() != null) {
            html.append(String.format(" | <a href=\"%s\">View Full Report</a>", summary.getPlatformAnalysisUrl()));
        }

        if (summary.getPullRequestUrl() != null) {
            html.append(String.format(" | <a href=\"%s\">Pull Request</a>", summary.getPullRequestUrl()));
        }

        html.append("</p>\n");
        html.append("</div>\n");

        html.append("</div>\n");

        return html.toString();
    }

    private void appendIssuesBySevertiy(StringBuilder html, List<AnalysisSummary.IssueSummary> issues,
                                        IssueSeverity severity, String cssClass, String title) {
        List<AnalysisSummary.IssueSummary> severityIssues = issues.stream()
                .filter(issue -> issue.getSeverity() == severity)
                .toList();

        if (severityIssues.isEmpty()) {
            return;
        }

        html.append(String.format("<div class=\"severity-group %s-severity\">\n", cssClass));
        html.append(String.format("<h4>%s</h4>\n", title));

        for (AnalysisSummary.IssueSummary issue : severityIssues) {
            html.append("<details class=\"issue-detail\">\n");
            html.append(String.format("<summary><strong>%s</strong> - %s</summary>\n",
                    escapeHtml(issue.getLocationDescription()),
                    escapeHtml(truncateText(issue.getReason(), 100))));

            html.append("<div class=\"issue-content\">\n");
            html.append(String.format("<p><strong>Issue:</strong> %s</p>\n", escapeHtml(issue.getReason())));

            if (issue.getSuggestedFix() != null && !issue.getSuggestedFix().trim().isEmpty()) {
                html.append("<p><strong>Suggested Fix:</strong></p>\n");
                html.append("<pre><code>");
                html.append(escapeHtml(issue.getSuggestedFix()));
                html.append("</code></pre>\n");
            }

            if (issue.getIssueUrl() != null) {
                html.append(String.format("<p><a href=\"%s\">View Issue Details</a></p>\n", issue.getIssueUrl()));
            }

            html.append("</div>\n");
            html.append("</details>\n");
        }

        html.append("</div>\n");
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

    private String escapeHtml(String text) {
        if (text == null) return "";
        return text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace("\"", "&quot;")
                .replace("'", "&#39;");
    }
}