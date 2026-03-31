package org.rostilos.codecrow.pipelineagent.qadoc;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.ProjectAiConnectionBinding;
import org.rostilos.codecrow.core.model.project.config.QaAutoDocConfig;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.PrEnrichmentDataDto;
import org.rostilos.codecrow.core.util.RetryExecutor;
import org.rostilos.codecrow.events.analysis.AnalysisCompletedEvent;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.taskmanagement.model.TaskDetails;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

/**
 * Service that calls the inference-orchestrator's {@code /qa-documentation} endpoint
 * to generate a QA-oriented summary of PR changes.
 * <p>
 * This service is responsible for:
 * <ul>
 *   <li>Assembling the request payload (analysis summary, task context, template config)</li>
 *   <li>Making the HTTP call with retry logic</li>
 *   <li>Parsing the response into a ready-to-post comment body</li>
 * </ul>
 */
@Service
public class QaDocGenerationService {

    private static final Logger log = LoggerFactory.getLogger(QaDocGenerationService.class);

    private final String inferenceOrchestratorUrl;
    private final ObjectMapper objectMapper;
    private final HttpClient httpClient;
    private final TokenEncryptionService tokenEncryptionService;

    public QaDocGenerationService(
            @Value("${codecrow.inference-orchestrator.url:http://inference-orchestrator:8000}") String inferenceOrchestratorUrl,
            TokenEncryptionService tokenEncryptionService) {
        this.inferenceOrchestratorUrl = inferenceOrchestratorUrl;
        this.tokenEncryptionService = tokenEncryptionService;
        this.objectMapper = new ObjectMapper();
        this.httpClient = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(30))
                .build();
    }

    /**
     * Generate QA documentation for a completed analysis.
     * <p>
     * Accepts a fully-populated {@link QaDocGenerationContext} carrying the diff,
     * delta-diff, enrichment data, VCS credentials, task context, and all metadata
     * needed by the inference-orchestrator's multi-stage pipeline.
     *
     * @param project the project
     * @param event   the analysis completed event (contains metrics with issue count, files, etc.)
     * @param ctx     the immutable generation context with all data
     * @return generated QA document text, or null if the LLM decided documentation isn't needed
     * @throws IOException if the inference orchestrator call fails after retries
     */
    public String generateQaDocumentation(Project project,
                                           AnalysisCompletedEvent event,
                                           QaDocGenerationContext ctx) throws IOException {
        Map<String, Object> payload = buildPayloadFromContext(project, event.getPrNumber(),
                event.getIssuesFound(), event.getFilesAnalyzed(), event.getMetrics(), ctx);
        return callInferenceOrchestrator(payload);
    }

    /**
     * Generate QA documentation from raw PR metadata — used by the {@code /codecrow qa-doc} command
     * processor when no {@link AnalysisCompletedEvent} is available.
     *
     * @param project       the project
     * @param prNumber      pull request number (may be null for branch-only PRs)
     * @param issuesFound   number of issues found in the latest analysis (0 if none)
     * @param filesAnalyzed number of files analyzed (0 if none)
     * @param prMetadata    a map with optional keys: sourceBranch, targetBranch, prTitle, prDescription
     * @param ctx           the immutable generation context with all data
     * @return generated QA document text, or null if the LLM decided documentation isn't needed
     * @throws IOException if the inference orchestrator call fails after retries
     */
    public String generateQaDocumentation(Project project,
                                           Long prNumber,
                                           int issuesFound,
                                           int filesAnalyzed,
                                           Map<String, Object> prMetadata,
                                           QaDocGenerationContext ctx) throws IOException {
        Map<String, Object> payload = buildPayloadFromContext(
                project, prNumber, issuesFound, filesAnalyzed, prMetadata, ctx);
        return callInferenceOrchestrator(payload);
    }

    /**
     * Shared HTTP call to the inference-orchestrator's /qa-documentation endpoint.
     */
    private String callInferenceOrchestrator(Map<String, Object> payload) throws IOException {
        String responseBody = RetryExecutor.withExponentialBackoff(() -> {
            String jsonPayload = objectMapper.writeValueAsString(payload);

            HttpRequest request = HttpRequest.newBuilder()
                    .uri(URI.create(inferenceOrchestratorUrl + "/qa-documentation"))
                    .header("Content-Type", "application/json")
                    .header("Accept", "application/json")
                    .POST(HttpRequest.BodyPublishers.ofString(jsonPayload))
                    .timeout(Duration.ofSeconds(300))
                    .build();

            HttpResponse<String> response;
            try {
                response = httpClient.send(request, HttpResponse.BodyHandlers.ofString());
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new IOException("QA doc generation interrupted", e);
            }

            if (response.statusCode() >= 500) {
                throw new IOException("Inference orchestrator returned " + response.statusCode());
            }
            if (response.statusCode() != 200) {
                log.warn("QA doc generation returned non-200: {} — {}", response.statusCode(), response.body());
                return null;
            }
            return response.body();
        });

        if (responseBody == null || responseBody.isBlank()) {
            return null;
        }

        // Parse the response
        try {
            JsonNode root = objectMapper.readTree(responseBody);
            String documentation = root.path("documentation").asText(null);
            boolean needed = root.path("documentation_needed").asBoolean(true);

            if (!needed) {
                log.info("QA auto-doc: LLM determined documentation is not needed");
                return null;
            }

            // Guard: reject null, blank, or error-sentinel documentation
            if (documentation == null || documentation.isBlank()
                    || documentation.contains("could not be generated")) {
                log.warn("QA auto-doc: documentation is null/blank/error-sentinel — treating as not generated");
                return null;
            }

            return documentation;
        } catch (Exception e) {
            // Reject unparseable responses — posting raw HTML/error pages to Jira
            // would corrupt the ticket. Return null so the caller skips the update.
            log.error("Failed to parse QA doc response as JSON (length={}): {}",
                    responseBody.length(), e.getMessage());
            return null;
        }
    }

    /**
     * Build the full HTTP payload from a {@link QaDocGenerationContext}.
     * <p>
     * This is the single payload-building method used by both the event-driven listener
     * and the command processor. It sends ALL data needed by the multi-stage Python pipeline:
     * diff (untruncated), delta-diff, enrichment data, VCS credentials, branch info, etc.
     */
    private Map<String, Object> buildPayloadFromContext(Project project,
                                                         Long prNumber,
                                                         int issuesFound,
                                                         int filesAnalyzed,
                                                         Map<String, Object> prMetadata,
                                                         QaDocGenerationContext ctx) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("project_id", project.getId());
        payload.put("project_name", project.getName());
        payload.put("pr_number", prNumber);
        payload.put("issues_found", issuesFound);
        payload.put("files_analyzed", filesAnalyzed);

        // ── AI credentials ──────────────────────────────────────────
        addAiCredentials(payload, project);

        // ── Full diff — NO truncation; the Python pipeline batches intelligently ──
        if (ctx.diff() != null && !ctx.diff().isBlank()) {
            payload.put("diff", ctx.diff());
        }

        // ── Delta diff (same-PR re-run: only the new changes since last analysis) ──
        if (ctx.deltaDiff() != null && !ctx.deltaDiff().isBlank()) {
            payload.put("delta_diff", ctx.deltaDiff());
        }

        // ── Enrichment data (file contents + AST metadata + dependency graph) ──
        // Serialized as-is by Jackson — PrEnrichmentDataDto is a record hierarchy
        if (ctx.enrichmentData() != null && ctx.enrichmentData().hasData()) {
            payload.put("enrichment_data", ctx.enrichmentData());
        }

        // ── Previous documentation (for multi-PR accumulation or same-PR delta) ──
        if (ctx.previousDocumentation() != null && !ctx.previousDocumentation().isBlank()) {
            payload.put("previous_documentation", ctx.previousDocumentation());
        }

        // ── Same-PR re-run flag ──
        payload.put("is_same_pr_rerun", ctx.isSamePrRerun());

        // ── Changed file paths (extracted from diff) ──
        if (ctx.changedFilePaths() != null && !ctx.changedFilePaths().isEmpty()) {
            payload.put("changed_file_paths", ctx.changedFilePaths());
        }

        // ── VCS connection info (for Python-side RAG queries) ──
        if (ctx.vcsProvider() != null) {
            payload.put("vcs_provider", ctx.vcsProvider());
        }
        if (ctx.workspaceSlug() != null) {
            payload.put("workspace_slug", ctx.workspaceSlug());
        }
        if (ctx.repoSlug() != null) {
            payload.put("repo_slug", ctx.repoSlug());
        }
        if (ctx.sourceBranch() != null) {
            payload.put("source_branch", ctx.sourceBranch());
        }
        if (ctx.targetBranch() != null) {
            payload.put("target_branch", ctx.targetBranch());
        }

        // ── OAuth credentials (for Python-side VCS/RAG access) ──
        if (ctx.oauthKey() != null) {
            payload.put("oauth_key", ctx.oauthKey());
        }
        if (ctx.oauthSecret() != null) {
            payload.put("oauth_secret", ctx.oauthSecret());
        }
        if (ctx.bearerToken() != null) {
            payload.put("bearer_token", ctx.bearerToken());
        }

        // ── PR metadata enriched with analysis summary ──
        Map<String, Object> prMeta = (prMetadata != null)
                ? new LinkedHashMap<>(prMetadata)
                : new LinkedHashMap<>();
        if (ctx.analysis() != null) {
            prMeta.put("analysisSummary", buildAnalysisSummary(ctx.analysis()));
        }
        if (!prMeta.isEmpty()) {
            payload.put("pr_metadata", prMeta);
        }

        // ── Template configuration ──
        QaAutoDocConfig qaConfig = ctx.qaConfig();
        if (qaConfig != null) {
            payload.put("template_mode", qaConfig.effectiveTemplateMode().name());
            if (qaConfig.effectiveTemplateMode() == QaAutoDocConfig.TemplateMode.CUSTOM
                    && qaConfig.customTemplate() != null) {
                payload.put("custom_template", qaConfig.customTemplate());
            }
            payload.put("output_language", qaConfig.effectiveOutputLanguage());
        }

        // ── Task context (if available) — keys match Python placeholder names ──
        TaskDetails taskDetails = ctx.taskDetails();
        if (taskDetails != null) {
            Map<String, String> taskContext = new LinkedHashMap<>();
            taskContext.put("task_key", taskDetails.taskId());
            taskContext.put("task_summary", taskDetails.summary());
            taskContext.put("description", taskDetails.description());
            taskContext.put("status", taskDetails.status());
            taskContext.put("task_type", taskDetails.taskType());
            taskContext.put("priority", taskDetails.priority());
            payload.put("task_context", taskContext);
        }

        return payload;
    }

    /**
     * Extract and decrypt the project's AI credentials, adding them to the payload
     * so the inference-orchestrator can create an LLM without server-level env vars.
     */
    private void addAiCredentials(Map<String, Object> payload, Project project) {
        try {
            ProjectAiConnectionBinding binding = project.getAiBinding();
            if (binding == null || binding.getAiConnection() == null) {
                log.warn("No AI connection configured for project {} — inference-orchestrator will fall back to env vars",
                        project.getId());
                return;
            }
            AIConnection aiConn = binding.getAiConnection();
            payload.put("ai_provider", aiConn.getProviderKey().name().toLowerCase());
            payload.put("ai_model", aiConn.getAiModel());
            payload.put("ai_api_key", tokenEncryptionService.decrypt(aiConn.getApiKeyEncrypted()));
            if (aiConn.getBaseUrl() != null) {
                payload.put("ai_base_url", aiConn.getBaseUrl());
            }
        } catch (Exception e) {
            log.warn("Failed to extract AI credentials for project {} — inference-orchestrator will fall back to env vars: {}",
                    project.getId(), e.getMessage());
        }
    }

    // ------------------------------------------------------------------
    // Analysis summary builder
    // ------------------------------------------------------------------

    /**
     * Build a rich, structured text summary from the code analysis issues.
     * This is what gets sent to the LLM as the {@code {analysis_summary}} placeholder,
     * replacing the previous "No analysis summary available." fallback.
     * <p>
     * The output is Markdown-formatted and grouped by file, with severity breakdown,
     * issue titles, reasons, and suggested fixes. Truncated to ~10 KB to avoid
     * exceeding LLM context limits.
     */
    static String buildAnalysisSummary(CodeAnalysis analysis) {
        if (analysis == null) {
            return "No analysis data available.";
        }

        List<CodeAnalysisIssue> issues = analysis.getIssues();
        if (issues == null || issues.isEmpty()) {
            return "No issues found in this analysis.";
        }

        List<CodeAnalysisIssue> activeIssues = issues.stream()
                .filter(i -> !i.isResolved())
                .toList();

        if (activeIssues.isEmpty()) {
            return "All " + issues.size() + " issues from this analysis have been resolved.";
        }

        StringBuilder sb = new StringBuilder();

        // ── Severity breakdown ──────────────────────────────────────
        sb.append("## Severity Breakdown\n");
        sb.append("- **HIGH**: ").append(analysis.getHighSeverityCount()).append('\n');
        sb.append("- **MEDIUM**: ").append(analysis.getMediumSeverityCount()).append('\n');
        sb.append("- **LOW**: ").append(analysis.getLowSeverityCount()).append('\n');
        sb.append("- **INFO**: ").append(analysis.getInfoSeverityCount()).append('\n');
        if (analysis.getResolvedCount() > 0) {
            sb.append("- **Resolved**: ").append(analysis.getResolvedCount()).append('\n');
        }
        sb.append('\n');

        // ── Issues grouped by file ──────────────────────────────────
        Map<String, List<CodeAnalysisIssue>> byFile = activeIssues.stream()
                .collect(Collectors.groupingBy(
                        CodeAnalysisIssue::getFilePath,
                        LinkedHashMap::new,
                        Collectors.toList()));

        sb.append("## Issues by File (").append(activeIssues.size())
          .append(" active across ").append(byFile.size()).append(" files)\n\n");

        for (Map.Entry<String, List<CodeAnalysisIssue>> entry : byFile.entrySet()) {
            sb.append("### `").append(entry.getKey()).append("`\n");

            for (CodeAnalysisIssue issue : entry.getValue()) {
                sb.append("- **[").append(issue.getSeverity()).append("]**");
                if (issue.getTitle() != null && !issue.getTitle().isBlank()) {
                    sb.append(' ').append(issue.getTitle());
                }
                if (issue.getIssueCategory() != null) {
                    sb.append(" _(").append(issue.getIssueCategory().getDisplayName()).append(")_");
                }
                sb.append('\n');

                // Reason / description
                if (issue.getReason() != null && !issue.getReason().isBlank()) {
                    sb.append("  ").append(truncate(issue.getReason(), 400)).append('\n');
                }

                // Suggested fix
                if (issue.getSuggestedFixDescription() != null && !issue.getSuggestedFixDescription().isBlank()) {
                    sb.append("  **Suggested fix**: ").append(truncate(issue.getSuggestedFixDescription(), 300)).append('\n');
                }

                // Line location
                if (issue.getLineNumber() != null) {
                    sb.append("  Line: ").append(issue.getLineNumber());
                    if (issue.getEndLineNumber() != null && !issue.getEndLineNumber().equals(issue.getLineNumber())) {
                        sb.append('-').append(issue.getEndLineNumber());
                    }
                    sb.append('\n');
                }
            }
            sb.append('\n');
        }

        // Truncate to prevent prompt bloat
        String result = sb.toString();
        if (result.length() > 10_000) {
            result = result.substring(0, 10_000)
                    + "\n\n... (truncated — " + activeIssues.size() + " total active issues across "
                    + byFile.size() + " files)";
        }
        return result;
    }

    private static String truncate(String text, int maxLength) {
        if (text == null || text.length() <= maxLength) return text;
        return text.substring(0, maxLength) + "…";
    }
}
