package org.rostilos.codecrow.pipelineagent.generic.processor.command;

import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.project.config.QaAutoDocConfig;
import org.rostilos.codecrow.core.model.taskmanagement.TaskManagementConnection;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;
import org.rostilos.codecrow.core.persistence.repository.taskmanagement.TaskManagementConnectionRepository;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsOperationsService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.rostilos.codecrow.pipelineagent.generic.dto.webhook.WebhookPayload;
import org.rostilos.codecrow.pipelineagent.generic.webhookhandler.CommentCommandWebhookHandler.CommentCommandProcessor;
import org.rostilos.codecrow.pipelineagent.generic.webhookhandler.WebhookHandler.WebhookResult;
import org.rostilos.codecrow.pipelineagent.qadoc.QaAutoDocListener;
import org.rostilos.codecrow.pipelineagent.qadoc.QaDocGenerationService;
import org.rostilos.codecrow.taskmanagement.ETaskManagementPlatform;
import org.rostilos.codecrow.taskmanagement.TaskManagementClient;
import org.rostilos.codecrow.taskmanagement.TaskManagementClientFactory;
import org.rostilos.codecrow.taskmanagement.model.TaskComment;
import org.rostilos.codecrow.taskmanagement.model.TaskDetails;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import okhttp3.OkHttpClient;

import java.util.*;
import java.util.function.Consumer;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Processor for the {@code /codecrow qa-doc} command.
 * <p>
 * Generates QA testing documentation for the current PR and posts it as a comment
 * on the linked Jira task. Reuses the same inference-orchestrator endpoint and
 * task management client infrastructure as the automatic {@link QaAutoDocListener}.
 * <p>
 * Usage:
 * <ul>
 *   <li>{@code /codecrow qa-doc} — auto-extract task ID from configured source (branch, PR title, etc.)</li>
 *   <li>{@code /codecrow qa-doc PROJ-123} — explicitly specify the target task key</li>
 * </ul>
 */
@Component("qaDocCommandProcessor")
public class QaDocCommandProcessor implements CommentCommandProcessor {

    private static final Logger log = LoggerFactory.getLogger(QaDocCommandProcessor.class);

    private final TaskManagementConnectionRepository connectionRepository;
    private final TaskManagementClientFactory clientFactory;
    private final QaDocGenerationService qaDocGenerationService;
    private final CodeAnalysisService codeAnalysisService;
    private final VcsClientProvider vcsClientProvider;
    private final VcsServiceFactory vcsServiceFactory;

    public QaDocCommandProcessor(
            TaskManagementConnectionRepository connectionRepository,
            TaskManagementClientFactory clientFactory,
            QaDocGenerationService qaDocGenerationService,
            CodeAnalysisService codeAnalysisService,
            VcsClientProvider vcsClientProvider,
            VcsServiceFactory vcsServiceFactory
    ) {
        this.connectionRepository = connectionRepository;
        this.clientFactory = clientFactory;
        this.qaDocGenerationService = qaDocGenerationService;
        this.codeAnalysisService = codeAnalysisService;
        this.vcsClientProvider = vcsClientProvider;
        this.vcsServiceFactory = vcsServiceFactory;
    }

    @Override
    public WebhookResult process(
            WebhookPayload payload,
            Project project,
            Consumer<Map<String, Object>> eventConsumer
    ) {
        return process(payload, project, eventConsumer, Map.of());
    }

    @Override
    public WebhookResult process(
            WebhookPayload payload,
            Project project,
            Consumer<Map<String, Object>> eventConsumer,
            Map<String, Object> additionalData
    ) {
        log.info("Processing qa-doc command for project={}, PR={}",
                project.getId(), payload.pullRequestId());

        try {
            // 1. Validate QA auto-doc is configured
            ProjectConfig config = project.getConfiguration();
            if (config == null || !config.isQaAutoDocEnabled()) {
                return WebhookResult.error(
                        "QA Auto-Documentation is not enabled for this project. " +
                        "Configure it in Project Settings → Task Management.");
            }

            QaAutoDocConfig qaConfig = config.getQaAutoDocConfig();
            if (!qaConfig.isFullyConfigured()) {
                return WebhookResult.error(
                        "QA Auto-Documentation is not fully configured. " +
                        "Please set up a task management connection and task ID pattern in Project Settings.");
            }

            eventConsumer.accept(Map.of(
                    "type", "status",
                    "state", "resolving_task",
                    "message", "Resolving task ID and connection..."
            ));

            // 2. Resolve task management connection
            TaskManagementConnection connection = connectionRepository
                    .findById(qaConfig.taskManagementConnectionId())
                    .orElse(null);
            if (connection == null) {
                return WebhookResult.error(
                        "Task management connection not found. It may have been deleted.");
            }

            TaskManagementClient client = createClient(connection);

            // 3. Extract or parse the task ID
            String explicitTaskId = (String) additionalData.get("taskId");
            String taskId;
            if (explicitTaskId != null && !explicitTaskId.isBlank()) {
                taskId = explicitTaskId.trim();
                log.info("Using explicitly provided task ID: {}", taskId);
            } else {
                taskId = extractTaskIdFromPayload(qaConfig, payload);
            }

            if (taskId == null || taskId.isBlank()) {
                return WebhookResult.error(
                        "Could not extract a task ID. Either provide one explicitly " +
                        "(`/codecrow qa-doc PROJ-123`) or configure task ID extraction in Project Settings.");
            }

            log.info("Resolved task ID: {} for project={}, PR={}",
                    taskId, project.getId(), payload.pullRequestId());

            eventConsumer.accept(Map.of(
                    "type", "status",
                    "state", "fetching_task",
                    "message", "Fetching task details for " + taskId + "..."
            ));

            // 4. Fetch task details from Jira for context
            TaskDetails taskDetails;
            try {
                taskDetails = client.getTaskDetails(taskId);
            } catch (Exception e) {
                log.warn("Failed to fetch task details for {}: {}", taskId, e.getMessage());
                taskDetails = null;
            }

            eventConsumer.accept(Map.of(
                    "type", "status",
                    "state", "generating_documentation",
                    "message", "Generating QA documentation..."
            ));

            // 5. Gather analysis metrics from latest analysis (if available)
            int issuesFound = 0;
            int filesAnalyzed = 0;
            CodeAnalysis analysis = null;
            Map<String, Object> prMetadata = buildPrMetadata(payload);
            Long prNumber = payload.pullRequestId() != null
                    ? Long.parseLong(payload.pullRequestId()) : null;

            if (prNumber != null) {
                // Use eager-loading query so issues are available for the analysis summary
                Optional<CodeAnalysis> latestAnalysis = codeAnalysisService
                        .getPreviousVersionCodeAnalysis(project.getId(), prNumber);
                if (latestAnalysis.isPresent()) {
                    analysis = latestAnalysis.get();
                    issuesFound = analysis.getTotalIssues();
                    filesAnalyzed = (int) analysis.getIssues().stream()
                            .map(i -> i.getFilePath())
                            .distinct()
                            .count();
                    log.debug("Found latest analysis: id={}, issues={}, files={}",
                            analysis.getId(), issuesFound, filesAnalyzed);
                }
            }

            // 5a. Fetch the raw PR diff from the VCS platform — critical context for QA
            String diff = null;
            try {
                VcsRepoInfo repoInfo = project.getEffectiveVcsRepoInfo();
                if (repoInfo != null && repoInfo.getVcsConnection() != null && prNumber != null) {
                    VcsConnection vcsConnection = repoInfo.getVcsConnection();
                    OkHttpClient httpClient = vcsClientProvider.getHttpClient(vcsConnection);
                    VcsOperationsService opsService = vcsServiceFactory.getOperationsService(
                            vcsConnection.getProviderType());
                    diff = opsService.getPullRequestDiff(
                            httpClient, repoInfo.getRepoWorkspace(),
                            repoInfo.getRepoSlug(), String.valueOf(prNumber));
                    log.info("qa-doc command: fetched PR diff, size={} chars",
                            diff != null ? diff.length() : 0);
                } else {
                    log.warn("qa-doc command: no VCS connection or PR number for project {}",
                            project.getId());
                }
            } catch (Exception e) {
                log.warn("qa-doc command: failed to fetch PR diff — proceeding without: {}",
                        e.getMessage());
            }

            // 6. Check for existing QA doc comment on this task (for multi-PR accumulation)
            Optional<TaskComment> existingComment = client.findCommentByMarker(taskId, QaAutoDocListener.COMMENT_MARKER_PREFIX);
            String previousDocumentation = null;

            if (existingComment.isPresent()) {
                String existingBody = existingComment.get().body();
                boolean isSamePrRerun = QaAutoDocListener.isCurrentPrAlreadyDocumented(existingBody, prNumber);

                if (!isSamePrRerun) {
                    // Different PR on the same task → pass previous doc to LLM for merging
                    previousDocumentation = existingBody;
                    log.info("qa-doc command: found existing comment for task {} from earlier PR(s) — will merge",
                            taskId);
                } else {
                    log.info("qa-doc command: re-analysis of same PR #{} — will overwrite", prNumber);
                }
            }

            // 7. Generate QA documentation via inference orchestrator
            String qaDocument = qaDocGenerationService.generateQaDocumentation(
                    project, prNumber, issuesFound, filesAnalyzed, prMetadata, qaConfig, taskDetails, analysis, diff,
                    previousDocumentation);

            if (qaDocument == null || qaDocument.isBlank()) {
                return WebhookResult.success(
                        "AI determined no QA documentation is needed for this change.",
                        Map.of("commandType", "qa-doc", "taskId", taskId, "documentationNeeded", false));
            }

            eventConsumer.accept(Map.of(
                    "type", "status",
                    "state", "posting_comment",
                    "message", "Posting documentation to " + taskId + "..."
            ));

            // 8. Post or update comment on Jira task
            String commentBody = QaAutoDocListener.COMMENT_MARKER + "\n\n" + qaDocument;

            if (existingComment.isPresent()) {
                client.updateComment(taskId, existingComment.get().commentId(), commentBody);
                log.info("qa-doc command: updated existing comment on task {} (commentId={})",
                        taskId, existingComment.get().commentId());
            } else {
                client.postComment(taskId, commentBody);
                log.info("qa-doc command: posted new comment on task {}", taskId);
            }

            eventConsumer.accept(Map.of(
                    "type", "status",
                    "state", "complete",
                    "message", "QA documentation posted to " + taskId
            ));

            String action = existingComment.isPresent() ? "updated" : "posted";
            String jiraUrl = taskDetails != null && taskDetails.webUrl() != null
                    ? taskDetails.webUrl() : taskId;

            return WebhookResult.success(
                    "QA documentation " + action + " on " + taskId,
                    Map.of(
                            "commandType", "qa-doc",
                            "taskId", taskId,
                            "action", action,
                            "content", buildVcsReplyContent(taskId, action, jiraUrl),
                            "documentationNeeded", true
                    ));

        } catch (Exception e) {
            log.error("Error processing qa-doc command: {}", e.getMessage(), e);
            return WebhookResult.error("Failed to generate QA documentation: " + e.getMessage());
        }
    }

    /**
     * Extract task ID from webhook payload using the configured regex pattern and source.
     */
    private String extractTaskIdFromPayload(QaAutoDocConfig config, WebhookPayload payload) {
        String source = switch (config.effectiveTaskIdSource()) {
            case BRANCH_NAME -> payload.sourceBranch();
            case PR_TITLE -> {
                if (payload.rawPayload() != null) {
                    // Bitbucket format
                    if (payload.rawPayload().has("pullrequest")) {
                        yield payload.rawPayload().path("pullrequest").path("title").asText(null);
                    }
                    // GitHub format
                    if (payload.rawPayload().has("pull_request")) {
                        yield payload.rawPayload().path("pull_request").path("title").asText(null);
                    }
                }
                yield null;
            }
            case PR_DESCRIPTION -> {
                if (payload.rawPayload() != null) {
                    // Bitbucket format (field: "description")
                    if (payload.rawPayload().has("pullrequest")) {
                        yield payload.rawPayload().path("pullrequest").path("description").asText(null);
                    }
                    // GitHub format (field: "body")
                    if (payload.rawPayload().has("pull_request")) {
                        yield payload.rawPayload().path("pull_request").path("body").asText(null);
                    }
                }
                yield null;
            }
        };

        if (source == null || source.isBlank()) return null;

        try {
            Pattern pattern = Pattern.compile(config.effectiveTaskIdPattern());
            Matcher matcher = pattern.matcher(source);
            if (matcher.find()) {
                return matcher.group();
            }
        } catch (Exception e) {
            log.warn("Invalid task ID pattern '{}': {}", config.taskIdPattern(), e.getMessage());
        }
        return null;
    }

    /**
     * Build PR metadata map from the webhook payload for the inference orchestrator.
     */
    private Map<String, Object> buildPrMetadata(WebhookPayload payload) {
        Map<String, Object> metadata = new LinkedHashMap<>();
        if (payload.sourceBranch() != null) metadata.put("sourceBranch", payload.sourceBranch());
        if (payload.targetBranch() != null) metadata.put("targetBranch", payload.targetBranch());
        if (payload.commitHash() != null) metadata.put("commitHash", payload.commitHash());

        // Extract PR title/description from raw payload if available
        if (payload.rawPayload() != null) {
            var pr = payload.rawPayload().path("pullrequest");
            if (!pr.isMissingNode()) {
                String title = pr.path("title").asText(null);
                String desc = pr.path("description").asText(null);
                if (title != null) metadata.put("prTitle", title);
                if (desc != null) metadata.put("prDescription", desc);
            }
            // GitHub format
            var ghPr = payload.rawPayload().path("pull_request");
            if (!ghPr.isMissingNode()) {
                String title = ghPr.path("title").asText(null);
                String body = ghPr.path("body").asText(null);
                if (title != null) metadata.put("prTitle", title);
                if (body != null) metadata.put("prDescription", body);
            }
        }

        return metadata;
    }

    /**
     * Build the VCS reply content that gets posted as a PR comment.
     */
    private String buildVcsReplyContent(String taskId, String action, String jiraUrl) {
        return "✅ **QA Documentation " + capitalize(action) + "**\n\n" +
               "QA testing documentation has been " + action +
               " on Jira task [" + taskId + "](" + jiraUrl + ").\n\n" +
               "_Generated by `/codecrow qa-doc`_";
    }

    private TaskManagementClient createClient(TaskManagementConnection conn) {
        ETaskManagementPlatform platform = ETaskManagementPlatform.fromId(conn.getProviderType().getId());
        Map<String, String> creds = conn.getCredentials();
        return clientFactory.createClient(
                platform,
                conn.getBaseUrl(),
                creds.getOrDefault("email", ""),
                creds.getOrDefault("apiToken", "")
        );
    }

    private static String capitalize(String s) {
        if (s == null || s.isEmpty()) return s;
        return Character.toUpperCase(s.charAt(0)) + s.substring(1);
    }
}
