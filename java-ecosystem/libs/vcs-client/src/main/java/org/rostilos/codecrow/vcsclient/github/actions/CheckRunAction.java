package org.rostilos.codecrow.vcsclient.github.actions;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import okhttp3.*;
import org.rostilos.codecrow.core.model.qualitygate.QualityGateResult;
import org.rostilos.codecrow.vcsclient.bitbucket.model.report.AnalysisSummary;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;

/**
 * Action to create GitHub Check Runs for code analysis results.
 * Check Runs appear in the GitHub UI under the "Checks" tab of a PR. Line-level
 * findings are intentionally left to native pull-request review comments so the
 * same issue is not rendered twice in the diff.
 */
public class CheckRunAction {
    
    private static final Logger log = LoggerFactory.getLogger(CheckRunAction.class);
    private static final String GITHUB_API_BASE = "https://api.github.com";
    private static final MediaType JSON = MediaType.parse("application/json; charset=utf-8");
    private static final ObjectMapper objectMapper = new ObjectMapper();
    
    private final OkHttpClient httpClient;
    
    public CheckRunAction(OkHttpClient httpClient) {
        this.httpClient = httpClient;
    }

    public void createCheckRun(String owner, String repo, String headSha, AnalysisSummary summary) throws IOException {
        String url = String.format("%s/repos/%s/%s/check-runs", GITHUB_API_BASE, owner, repo);
        
        ObjectNode requestBody = buildCheckRunRequest(headSha, summary);
        
        Request request = new Request.Builder()
                .url(url)
                .post(RequestBody.create(objectMapper.writeValueAsString(requestBody), JSON))
                .header("Accept", "application/vnd.github+json")
                .header("X-GitHub-Api-Version", "2022-11-28")
                .build();
        
        try (Response response = httpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                String errorBody = response.body() != null ? response.body().string() : "No body";
                log.error("Failed to create Check Run: {} - {}", response.code(), errorBody);
                throw new IOException("Failed to create Check Run: " + response.code() + " - " + errorBody);
            }
            
            log.info("Successfully created Check Run for commit {}", headSha);
        }
    }
    
    private ObjectNode buildCheckRunRequest(String headSha, AnalysisSummary summary) {
        ObjectNode root = objectMapper.createObjectNode();
        
        root.put("name", "CodeCrow Analysis");
        root.put("head_sha", headSha);
        root.put("status", "completed");
        
        String conclusion = determineConclusion(summary);
        root.put("conclusion", conclusion);
        
        root.put("started_at", ZonedDateTime.now().minusMinutes(1).format(DateTimeFormatter.ISO_INSTANT));
        root.put("completed_at", ZonedDateTime.now().format(DateTimeFormatter.ISO_INSTANT));
        
        ObjectNode output = objectMapper.createObjectNode();
        output.put("title", buildTitle(summary));
        output.put("summary", buildSummaryText(summary));
        output.put("text", buildDetailedText(summary));
        
        root.set("output", output);
        
        return root;
    }
    
    /**
     * Determines GitHub Check Run conclusion from the quality gate result in the summary.
     * Falls back to count-based logic when no quality gate is configured.
     *
     * GitHub conclusions: "success", "failure", "neutral", "cancelled", "skipped", "timed_out", "action_required"
     */
    private String determineConclusion(AnalysisSummary summary) {
        QualityGateResult qgResult = summary.getQualityGateResult();
        if (qgResult != null && !qgResult.isSkipped()) {
            return qgResult.isPassed() ? "success" : "failure";
        }
        // Fallback when no quality gate is configured
        if (summary.getTotalUnresolvedIssues() == 0) {
            return "success";
        }
        
        if (summary.getHighSeverityIssues() != null && summary.getHighSeverityIssues().getCount() > 0) {
            return "failure";
        }
        
        if (summary.getMediumSeverityIssues() != null && summary.getMediumSeverityIssues().getCount() > 0) {
            return "neutral";
        }
        
        return "neutral";
    }
    
    private String buildTitle(AnalysisSummary summary) {
        if (summary.getTotalUnresolvedIssues() == 0) {
            return "✅ No issues found";
        }
        
        return String.format("⚠️ Found %d issue(s)", summary.getTotalUnresolvedIssues());
    }
    
    private String buildSummaryText(AnalysisSummary summary) {
        StringBuilder sb = new StringBuilder();
        
        sb.append("## CodeCrow Code Analysis\n\n");
        
        if (summary.getTotalUnresolvedIssues() == 0) {
            sb.append("✅ **No issues found!** Your code looks great.\n");
        } else {
            sb.append("| Severity | Count |\n");
            sb.append("|----------|-------|\n");
            
            if (summary.getHighSeverityIssues() != null && summary.getHighSeverityIssues().getCount() > 0) {
                sb.append(String.format("| 🔴 High | %d |\n", summary.getHighSeverityIssues().getCount()));
            }
            if (summary.getMediumSeverityIssues() != null && summary.getMediumSeverityIssues().getCount() > 0) {
                sb.append(String.format("| 🟡 Medium | %d |\n", summary.getMediumSeverityIssues().getCount()));
            }
            if (summary.getLowSeverityIssues() != null && summary.getLowSeverityIssues().getCount() > 0) {
                sb.append(String.format("| 🔵 Low | %d |\n", summary.getLowSeverityIssues().getCount()));
            }
            if (summary.getResolvedIssues() != null && summary.getResolvedIssues().getCount() > 0) {
                sb.append(String.format("| ✅ Resolved | %d |\n", summary.getResolvedIssues().getCount()));
            }
        }
        
        if (summary.getPlatformAnalysisUrl() != null) {
            sb.append(String.format("\n[View Full Report](%s)\n", summary.getPlatformAnalysisUrl()));
        }
        
        return sb.toString();
    }
    
    private String buildDetailedText(AnalysisSummary summary) {
        StringBuilder sb = new StringBuilder();
        
        if (summary.getComment() != null && !summary.getComment().trim().isEmpty()) {
            sb.append("### Analysis Summary\n\n");
            sb.append(summary.getComment()).append("\n\n");
        }
        
        if (!summary.getFileIssueCount().isEmpty()) {
            sb.append("### Files Affected\n\n");
            summary.getFileIssueCount().entrySet().stream()
                    .sorted((a, b) -> b.getValue().compareTo(a.getValue()))
                    .limit(10)
                    .forEach(entry -> {
                        sb.append(String.format("- `%s`: %d issue(s)\n", entry.getKey(), entry.getValue()));
                    });
        }
        
        return sb.toString();
    }
    
}
