package org.rostilos.codecrow.pipelineagent.generic.processor.command;

import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.PrEnrichmentDataDto;
import org.rostilos.codecrow.analysisengine.service.pr.PrFileEnrichmentService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsOperationsService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.analysisengine.util.DiffContentFilter;
import org.rostilos.codecrow.analysisengine.util.DiffParser;
import org.rostilos.codecrow.analysisengine.util.VcsDiffUtils;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.project.config.QaAutoDocConfig;
import org.rostilos.codecrow.core.model.qadoc.QaDocState;
import org.rostilos.codecrow.core.model.taskmanagement.TaskManagementConnection;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;
import org.rostilos.codecrow.core.persistence.repository.qadoc.QaDocStateRepository;
import org.rostilos.codecrow.core.persistence.repository.taskmanagement.TaskManagementConnectionRepository;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.pipelineagent.generic.dto.webhook.WebhookPayload;
import org.rostilos.codecrow.pipelineagent.generic.webhookhandler.CommentCommandWebhookHandler.CommentCommandProcessor;
import org.rostilos.codecrow.pipelineagent.generic.webhookhandler.WebhookHandler.WebhookResult;
import org.rostilos.codecrow.pipelineagent.qadoc.QaAutoDocListener;
import org.rostilos.codecrow.pipelineagent.qadoc.QaDocGenerationContext;
import org.rostilos.codecrow.pipelineagent.qadoc.QaDocGenerationService;
import org.rostilos.codecrow.taskmanagement.ETaskManagementPlatform;
import org.rostilos.codecrow.taskmanagement.TaskManagementClient;
import org.rostilos.codecrow.taskmanagement.TaskManagementClientFactory;
import org.rostilos.codecrow.taskmanagement.TaskManagementException;
import org.rostilos.codecrow.taskmanagement.model.TaskComment;
import org.rostilos.codecrow.taskmanagement.model.TaskDetails;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.vcsclient.utils.VcsConnectionCredentialsExtractor;
import org.rostilos.codecrow.vcsclient.utils.VcsConnectionCredentialsExtractor.VcsConnectionCredentials;
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
    private final QaDocStateRepository qaDocStateRepository;
    private final PrFileEnrichmentService enrichmentService;
    private final VcsConnectionCredentialsExtractor credentialsExtractor;

    public QaDocCommandProcessor(
            TaskManagementConnectionRepository connectionRepository,
            TaskManagementClientFactory clientFactory,
            QaDocGenerationService qaDocGenerationService,
            CodeAnalysisService codeAnalysisService,
            VcsClientProvider vcsClientProvider,
            VcsServiceFactory vcsServiceFactory,
            QaDocStateRepository qaDocStateRepository,
            PrFileEnrichmentService enrichmentService,
            TokenEncryptionService tokenEncryptionService
    ) {
        this.connectionRepository = connectionRepository;
        this.clientFactory = clientFactory;
        this.qaDocGenerationService = qaDocGenerationService;
        this.codeAnalysisService = codeAnalysisService;
        this.vcsClientProvider = vcsClientProvider;
        this.vcsServiceFactory = vcsServiceFactory;
        this.qaDocStateRepository = qaDocStateRepository;
        this.enrichmentService = enrichmentService;
        this.credentialsExtractor = new VcsConnectionCredentialsExtractor(tokenEncryptionService);
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
            } catch (TaskManagementException e) {
                if (e.getStatusCode() == 404) {
                    log.error("Task {} not found in Jira (HTTP 404). " +
                            "Check that the task exists and the API token has access.", taskId);
                    return WebhookResult.error(
                            "Task " + taskId + " was not found in Jira. " +
                            "Please verify that the task exists and the configured API token has access to it.");
                }
                if (e.isAuthError()) {
                    log.error("Authentication failed when fetching task {}: {}", taskId, e.getMessage());
                    return WebhookResult.error(
                            "Jira authentication failed for task " + taskId + ". " +
                            "Please check the API token in your task management connection settings.");
                }
                log.warn("Failed to fetch task details for {}: {}", taskId, e.getMessage());
                taskDetails = null;
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

            // 5a. Resolve VCS connection info
            VcsRepoInfo repoInfo = project.getEffectiveVcsRepoInfo();
            VcsConnection vcsConnection = (repoInfo != null) ? repoInfo.getVcsConnection() : null;
            String workspace = (repoInfo != null) ? repoInfo.getRepoWorkspace() : null;
            String repoSlug = (repoInfo != null) ? repoInfo.getRepoSlug() : null;
            String commitHash = payload.commitHash();
            String sourceBranch = payload.sourceBranch();
            String targetBranch = payload.targetBranch();

            // 5b. Fetch the raw PR diff from the VCS platform
            String diff = null;
            OkHttpClient httpClient = null;
            VcsOperationsService opsService = null;

            if (vcsConnection != null && prNumber != null) {
                try {
                    httpClient = vcsClientProvider.getHttpClient(vcsConnection);
                    opsService = vcsServiceFactory.getOperationsService(vcsConnection.getProviderType());
                    diff = opsService.getPullRequestDiff(
                            httpClient, workspace, repoSlug, String.valueOf(prNumber));
                    log.info("qa-doc command: fetched PR diff, size={} chars",
                            diff != null ? diff.length() : 0);
                } catch (Exception e) {
                    log.warn("qa-doc command: failed to fetch PR diff — proceeding without: {}",
                            e.getMessage());
                }
            }

            // 5c. Extract changed file paths from diff
            List<String> changedFilePaths = Collections.emptyList();
            if (diff != null && !diff.isBlank()) {
                try {
                    changedFilePaths = DiffParser.extractChangedFiles(diff);
                } catch (Exception e) {
                    log.warn("qa-doc command: failed to extract changed files: {}", e.getMessage());
                }
            }

            // 5d. Fetch enrichment data (file contents + AST metadata)
            PrEnrichmentDataDto enrichmentData = PrEnrichmentDataDto.empty();
            if (vcsConnection != null && !changedFilePaths.isEmpty()) {
                enrichmentData = fetchEnrichmentData(
                        vcsConnection, workspace, repoSlug, commitHash, changedFilePaths);
            }

            // 5e. Extract VCS credentials for Python-side RAG access
            String oauthKey = null;
            String oauthSecret = null;
            String bearerToken = null;
            String vcsProviderStr = null;
            if (vcsConnection != null) {
                try {
                    VcsConnectionCredentials creds = credentialsExtractor.extractCredentials(vcsConnection);
                    oauthKey = creds.oAuthClient();
                    oauthSecret = creds.oAuthSecret();
                    bearerToken = creds.accessToken();
                    vcsProviderStr = creds.vcsProviderString();
                } catch (Exception e) {
                    log.warn("qa-doc command: failed to extract VCS credentials: {}", e.getMessage());
                }
            }

            // 6. Load server-side state and check for existing Jira comment
            QaDocState state = (prNumber != null)
                    ? qaDocStateRepository.findByProjectIdAndTaskId(project.getId(), taskId).orElse(null)
                    : null;
            boolean isSamePrRerun = (state != null && prNumber != null && state.isPrDocumented(prNumber));

            Optional<TaskComment> existingComment = Optional.empty();
            try {
                existingComment = client.findCommentByMarker(
                        taskId, QaAutoDocListener.COMMENT_MARKER_PREFIX);
            } catch (Exception e) {
                log.warn("qa-doc command: failed to fetch existing comments for task {} — will post new comment: {}",
                        taskId, e.getMessage());
            }
            String previousDocumentation = null;

            if (existingComment.isPresent()) {
                if (!isSamePrRerun) {
                    previousDocumentation = existingComment.get().body();
                    log.info("qa-doc command: found existing comment for task {} from earlier PR(s) — will merge",
                            taskId);
                } else {
                    // Same-PR re-run: pass previous doc for targeted delta update
                    previousDocumentation = existingComment.get().body();
                    log.info("qa-doc command: re-analysis of same PR #{} — will produce delta update",
                            prNumber);
                }
            }

            // 6a. Compute delta diff for same-PR re-runs
            String deltaDiff = null;
            if (isSamePrRerun && state.getLastCommitHash() != null
                    && commitHash != null && opsService != null && httpClient != null) {
                DiffContentFilter contentFilter = new DiffContentFilter();
                final OkHttpClient cl = httpClient;
                final VcsOperationsService ops = opsService;
                deltaDiff = VcsDiffUtils.fetchDeltaDiff(
                        (ws, repo, base, head) -> ops.getCommitRangeDiff(cl, ws, repo, base, head),
                        workspace, repoSlug,
                        state.getLastCommitHash(), commitHash, contentFilter);
                if (deltaDiff != null) {
                    log.info("qa-doc command: computed delta diff ({} chars) for same-PR re-run",
                            deltaDiff.length());
                }
            }

            // 7. Build generation context and generate QA documentation
            QaDocGenerationContext ctx = QaDocGenerationContext.builder()
                    .qaConfig(qaConfig)
                    .taskDetails(taskDetails)
                    .analysis(analysis)
                    .diff(diff)
                    .deltaDiff(deltaDiff)
                    .enrichmentData(enrichmentData)
                    .changedFilePaths(changedFilePaths)
                    .previousDocumentation(previousDocumentation)
                    .isSamePrRerun(isSamePrRerun)
                    .vcsProvider(vcsProviderStr)
                    .workspaceSlug(workspace)
                    .repoSlug(repoSlug)
                    .sourceBranch(sourceBranch)
                    .targetBranch(targetBranch)
                    .oauthKey(oauthKey)
                    .oauthSecret(oauthSecret)
                    .bearerToken(bearerToken)
                    .build();

            String qaDocument = qaDocGenerationService.generateQaDocumentation(
                    project, prNumber, issuesFound, filesAnalyzed, prMetadata, ctx);

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
            String action;

            try {
                if (existingComment.isPresent()) {
                    client.updateComment(taskId, existingComment.get().commentId(), commentBody);
                    log.info("qa-doc command: updated existing comment on task {} (commentId={})",
                            taskId, existingComment.get().commentId());
                    action = "updated";
                } else {
                    client.postComment(taskId, commentBody);
                    log.info("qa-doc command: posted new comment on task {}", taskId);
                    action = "posted";
                }
            } catch (Exception e) {
                log.error("qa-doc command: failed to post/update comment on task {}: {}",
                        taskId, e.getMessage(), e);
                return WebhookResult.error(
                        "QA documentation was generated but could not be posted to " + taskId + ": " + e.getMessage());
            }

            // 8a. Upsert server-side QA doc state
            if (prNumber != null) {
                try {
                    QaDocState docState = (state != null) ? state : new QaDocState(project, taskId);
                    Long analysisId = (analysis != null) ? analysis.getId() : null;
                    docState.recordGeneration(commitHash, analysisId, prNumber);
                    qaDocStateRepository.save(docState);
                    log.debug("qa-doc command: persisted state for task {} (PRs={})",
                            taskId, docState.getDocumentedPrNumbers());
                } catch (Exception e) {
                    log.warn("qa-doc command: failed to persist state: {}", e.getMessage());
                }
            }

            eventConsumer.accept(Map.of(
                    "type", "status",
                    "state", "complete",
                    "message", "QA documentation posted to " + taskId
            ));

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

    // ── Enrichment helper ─────────────────────────────────────────

    /**
     * Fetch enrichment data with graceful fallback (same pattern as QaAutoDocListener).
     */
    private PrEnrichmentDataDto fetchEnrichmentData(VcsConnection vcsConnection,
                                                     String workspace,
                                                     String repoSlug,
                                                     String commitHash,
                                                     List<String> changedFiles) {
        VcsClient vcsClient;
        try {
            vcsClient = vcsClientProvider.getClient(vcsConnection);
        } catch (Exception e) {
            log.warn("qa-doc command: failed to obtain VCS client for enrichment: {}", e.getMessage());
            return PrEnrichmentDataDto.empty();
        }

        if (enrichmentService != null && enrichmentService.isEnrichmentEnabled()) {
            try {
                PrEnrichmentDataDto data = enrichmentService.enrichPrFiles(
                        vcsClient, workspace, repoSlug, commitHash, changedFiles);
                if (data != null && data.hasData()) {
                    log.info("qa-doc command: enrichment complete — {} files",
                            data.fileContents() != null ? data.fileContents().size() : 0);
                    return data;
                }
            } catch (Exception e) {
                log.warn("qa-doc command: full enrichment failed: {}", e.getMessage());
            }

            try {
                PrEnrichmentDataDto fallback = enrichmentService.fetchFileContentsOnly(
                        vcsClient, workspace, repoSlug, commitHash, changedFiles);
                if (fallback != null && fallback.hasData()) {
                    log.info("qa-doc command: using file-contents-only fallback enrichment");
                    return fallback;
                }
            } catch (Exception e) {
                log.warn("qa-doc command: fallback enrichment failed: {}", e.getMessage());
            }
        }
        return PrEnrichmentDataDto.empty();
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
