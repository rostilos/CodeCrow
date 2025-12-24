package org.rostilos.codecrow.vcsclient.bitbucket.service;

import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.dto.request.CloudCreateReportRequest;
import org.rostilos.codecrow.vcsclient.bitbucket.model.report.*;
import org.rostilos.codecrow.vcsclient.bitbucket.model.report.formatters.MarkdownAnalysisFormatter;
import org.rostilos.codecrow.vcsclient.bitbucket.model.report.formatters.PlainTextAnalysisFormatter;
import org.rostilos.codecrow.vcsclient.utils.LinksGenerator;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;


import java.util.*;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.stream.Collectors;

import static java.util.stream.Collectors.toSet;

@Component
public class ReportGenerator {
    private static final String TITLE = "CodeCrow";
    private static final String REPORTER = "CodeCrow";
    private static final String LINK_TEXT = "Go to CodeCrow";

    @Value("${codecrow.frontend-url:http://localhost:8080}")
    private String baseUrl;
    //private final String baseUrl = "https://codecrow.rostilos.pp.ua";

    private static final Logger log = LoggerFactory.getLogger(ReportGenerator.class);
    /**
     * Creates an AnalysisSummary from a CodeAnalysis entity
     *
     * @param analysis The code analysis entity
     * @return AnalysisSummary with formatted data
     */
    public AnalysisSummary createAnalysisSummary(CodeAnalysis analysis, Long platformPrEntityId) {
        try {
            Project project = analysis.getProject();
            List<CodeAnalysisIssue> issues = analysis.getIssues();

            // Split into resolved and unresolved
            List<CodeAnalysisIssue> resolvedIssues = issues.stream()
                    .filter(CodeAnalysisIssue::isResolved)
                    .toList();

            List<CodeAnalysisIssue> unresolvedIssues = issues.stream()
                    .filter(issue -> !issue.isResolved())
                    .toList();

            // Group unresolved issues by severity
            Map<IssueSeverity, List<CodeAnalysisIssue>> unresolvedBySeverity = unresolvedIssues.stream()
                    .collect(Collectors.groupingBy(CodeAnalysisIssue::getSeverity));

            // Count resolved issues
            int resolvedCount = resolvedIssues.size();

            // Count issues by file (both resolved and unresolved)
            Map<String, Integer> fileIssueCount = issues.stream()
                    .collect(Collectors.groupingBy(
                            issue -> issue.getFilePath() != null ? issue.getFilePath() : "unknown",
                            Collectors.collectingAndThen(Collectors.counting(), Math::toIntExact)
                    ));

            // Create severity metrics for unresolved issues only
            AnalysisSummary.SeverityMetric highSeverityMetric = createSeverityMetric(
                    IssueSeverity.HIGH,
                    unresolvedBySeverity.getOrDefault(IssueSeverity.HIGH, List.of()).size(),
                    project,
                    platformPrEntityId,
                    analysis.getPrVersion()
            );

            AnalysisSummary.SeverityMetric mediumSeverityMetric = createSeverityMetric(
                    IssueSeverity.MEDIUM,
                    unresolvedBySeverity.getOrDefault(IssueSeverity.MEDIUM, List.of()).size(),
                    project,
                    platformPrEntityId,
                    analysis.getPrVersion()
            );

            AnalysisSummary.SeverityMetric lowSeverityMetric = createSeverityMetric(
                    IssueSeverity.LOW,
                    unresolvedBySeverity.getOrDefault(IssueSeverity.LOW, List.of()).size(),
                    project,
                    platformPrEntityId,
                    analysis.getPrVersion()
            );

            AnalysisSummary.SeverityMetric resolvedSeverityMetric = createSeverityMetric(
                    IssueSeverity.RESOLVED,
                    resolvedCount,
                    project,
                    platformPrEntityId,
                    analysis.getPrVersion()
            );

            // Create issue summaries (for both resolved & unresolved)
            List<AnalysisSummary.IssueSummary> issueSummaries = issues.stream()
                    .filter(issue -> !issue.isResolved())
                    .map(issue -> new AnalysisSummary.IssueSummary(
                            issue.getSeverity(),
                            issue.getFilePath(),
                            issue.getLineNumber(),
                            issue.getReason(),
                            issue.getSuggestedFixDescription(),
                            LinksGenerator.createIssueUrl(baseUrl, project, issue.getId()),
                            issue.getId()
                    ))
                    .toList();

            // Build the summary
            return AnalysisSummary.builder()
                    .withProjectNamespace(project.getNamespace())
                    .withPullRequestId(analysis.getPrNumber())
                    .withComment(analysis.getComment())
                    .withPlatformAnalysisUrl(LinksGenerator.createPlatformAnalysisUrl(baseUrl, analysis, platformPrEntityId))
                    .withPullRequestUrl(LinksGenerator.createPullRequestUrl(analysis))
                    .withAnalysisDate(analysis.getCreatedAt())
                    .withHighSeverityIssues(highSeverityMetric)
                    .withMediumSeverityIssues(mediumSeverityMetric)
                    .withLowSeverityIssues(lowSeverityMetric)
                    .withResolvedIssues(resolvedSeverityMetric)
                    .withTotalIssues(issues.size())
                    .withTotalUnresolvedIssues(unresolvedIssues.size())
                    .withIssues(issueSummaries)
                    .withFileIssueCount(fileIssueCount)
                    .build();

        } catch (Exception e) {
            log.error("Error creating analysis summary for analysis ID {}: {}", analysis.getId(), e.getMessage(), e);
            throw new RuntimeException("Failed to create analysis summary", e);
        }
    }

    /**
     * Creates a summary for a single issue
     *
     * @param issue The code analysis issue
     * @param project The project
     * @param analysisId The analysis ID
     * @return IssueSummary
     */
    public AnalysisSummary.IssueSummary createIssueSummary(CodeAnalysisIssue issue, Project project, Long analysisId) {
        return new AnalysisSummary.IssueSummary(
                issue.getSeverity(),
                issue.getFilePath(),
                issue.getLineNumber(),
                issue.getReason(),
                issue.getSuggestedFixDescription(),
                LinksGenerator.createIssueUrl(baseUrl, project, issue.getId()),
                issue.getId()
        );
    }

    /**
     * Creates severity metric with count and URL
     */
    private AnalysisSummary.SeverityMetric createSeverityMetric(IssueSeverity severity, int count, Project project, Long platformPrEntityId, int prVersion) {
        String url;
        if(severity == IssueSeverity.RESOLVED) {
            url = LinksGenerator.createStatusUrl(baseUrl, project, platformPrEntityId, prVersion, "resolved");
        } else {
            url = LinksGenerator.createSeverityUrl(baseUrl, project, platformPrEntityId, severity, prVersion);
        }

        return new AnalysisSummary.SeverityMetric(severity, count, url);
    }


    /**
     * Creates a markdown-formatted summary for pull request comments
     *
     * @param analysis The analysis to format
     * @return Markdown-formatted string
     */
    public String createMarkdownSummary(CodeAnalysis analysis, AnalysisSummary summary) {
        try {
            return new MarkdownAnalysisFormatter().format(summary);
        } catch (Exception e) {
            log.error("Error creating markdown summary for analysis {}: {}", analysis.getId(), e.getMessage(), e);
            return createFallbackSummary(analysis);
        }
    }

    /**
     * Creates a plain text summary for pull request comments
     *
     * @param analysis The analysis to format
     * @return Plain text formatted string
     */
    public String createPlainTextSummary(CodeAnalysis analysis, Long platformPrEntityId) {
        try {
            AnalysisSummary summary = createAnalysisSummary(analysis, platformPrEntityId);
            return new PlainTextAnalysisFormatter().format(summary);
        } catch (Exception e) {
            log.error("Error creating plain text summary for analysis {}: {}", analysis.getId(), e.getMessage(), e);
            return createFallbackSummary(analysis);
        }
    }

    /**
     * Creates a fallback summary when formatting fails
     */
    private String createFallbackSummary(CodeAnalysis analysis) {
        int issueCount = analysis.getIssues().size();
        if (issueCount == 0) {
            return "✅ Code analysis completed - no issues found.";
        } else {
            return String.format("⚠️ Code analysis completed - found %d issue%s.",
                    issueCount, issueCount == 1 ? "" : "s");
        }
    }

    public CodeInsightsReport createCodeInsightsReport(AnalysisSummary summary, CodeAnalysis analysis) {
        return new CloudCreateReportRequest(
                toReport(summary),
                reportDescription(),
                TITLE,
                REPORTER,
                Date.from(analysis.getCreatedAt().toInstant()),
                summary.getPlatformAnalysisUrl(),
                LinksGenerator.createMediaFileUrl(baseUrl, "logo.png"),
                "COVERAGE",
                summary.getTotalIssues() > 0 ? "FAILED" : "PASSED"
        );

        //upload report action
    }

    private List<ReportData> toReport(AnalysisSummary summary) {
        List<ReportData> reportData = new ArrayList<>();
        reportData.add(new ReportData("New Issues", new DataValue.Text(
                issueLabel(summary.getTotalUnresolvedIssues()))
        ));
        reportData.add(new ReportData("High severity issues", new DataValue.Text(
                issueLabel(summary.getHighSeverityIssues().getCount()))
        ));
        reportData.add(new ReportData("Medium severity issues", new DataValue.Text(
                issueLabel(summary.getMediumSeverityIssues().getCount()))
        ));
        reportData.add(new ReportData("Low severity issues", new DataValue.Text(
                issueLabel(summary.getLowSeverityIssues().getCount()))
        ));
        reportData.add(new ReportData("Resolved issues", new DataValue.Text(
                issueLabel(summary.getResolvedIssues().getCount()))
        ));
        reportData.add(new ReportData("Analysis details", new DataValue.CloudLink(LINK_TEXT, summary.getPlatformAnalysisUrl())));
        return reportData;
    }

    private static String issueLabel(long count) {
        return count + (count == 1 ? " Issue" : " Issues");
    }

    private static String reportDescription() {
       return "Codecrow analysis processed.";
    }

    public Set<CodeInsightsAnnotation> createReportAnnotations(CodeAnalysis analysis, Project project) {
        final AtomicInteger chunkCounter = new AtomicInteger(0);
        int maxDescriptionLength = 400;
        //client.deleteAnnotations(analysisDetails.getCommitSha(), reportKey);
        //AnnotationUploadLimit uploadLimit = client.getAnnotationUploadLimit();

       return analysis.getIssues().stream()
                .filter(componentIssue -> !componentIssue.isResolved())
                .map(componentIssue -> {
                    String path = componentIssue.getFilePath();
                    String description = componentIssue.getReason().length() > maxDescriptionLength
                            ? componentIssue.getReason().substring(0, maxDescriptionLength)
                            : componentIssue.getReason();
                    //AnalysisIssueSummary analysisIssueSummary = reportGenerator.createAnalysisIssueSummary(componentIssue, analysisDetails);
                    //Map.Entry<SoftwareQuality, Severity> highestSeverity = findHighestSeverity(componentIssue.getIssue().impacts());
                    return createCodeInsightsAnnotation(
                            componentIssue.getId().toString(),
                            componentIssue.getLineNumber(),
                            LinksGenerator.createIssueUrl(baseUrl, project, componentIssue.getId()),
                            description,
                            path,
                            toBitbucketSeverity(componentIssue.getSeverity()),
                            "CODE_SMELL");
                }).collect(toSet());
    }

    public CodeInsightsAnnotation createCodeInsightsAnnotation(
            String issueKey,
            int line,
            String issueUrl,
            String message,
            String path,
            String severity,
            String type
    ) {
        return new CloudAnnotation(issueKey,
                line,
                issueUrl,
                message,
                path,
                severity,
                type);
    }

    private static String toBitbucketSeverity(IssueSeverity severity) {
        return switch (severity) {
            case HIGH -> "HIGH";
            case MEDIUM -> "MEDIUM";
            default -> "LOW";
        };
    }
}
