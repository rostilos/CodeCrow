package org.rostilos.codecrow.pipelineagent.qadoc;

import okhttp3.OkHttpClient;
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
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.core.persistence.repository.qadoc.QaDocStateRepository;
import org.rostilos.codecrow.core.persistence.repository.taskmanagement.TaskManagementConnectionRepository;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.events.analysis.AnalysisCompletedEvent;
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
import org.springframework.context.event.EventListener;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.util.*;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Listens for {@link AnalysisCompletedEvent} and triggers QA auto-documentation
 * generation when the feature is enabled for the project.
 * <p>
 * This is a <strong>non-blocking, non-critical</strong> post-processing step.
 * Failures here never block or affect the PR review result.
 * <p>
 * State tracking (which PRs have been documented, the last-analyzed commit hash)
 * is stored server-side in the {@code qa_doc_state} table — never derived from
 * Jira comment markers, which can be edited by any user with access.
 */
@Component
public class QaAutoDocListener {

    private static final Logger log = LoggerFactory.getLogger(QaAutoDocListener.class);

    /** Hidden marker embedded in auto-doc comments for detection/replacement. */
    public static final String COMMENT_MARKER = "<!-- codecrow-qa-autodoc -->";

    /** Prefix of the PR-tracking marker variant, e.g. {@code <!-- codecrow-qa-autodoc:prs=42,57 -->}. */
    public static final String COMMENT_MARKER_PREFIX = "<!-- codecrow-qa-autodoc";

    private final ProjectRepository projectRepository;
    private final TaskManagementConnectionRepository connectionRepository;
    private final TaskManagementClientFactory clientFactory;
    private final QaDocGenerationService qaDocGenerationService;
    private final CodeAnalysisService codeAnalysisService;
    private final VcsClientProvider vcsClientProvider;
    private final VcsServiceFactory vcsServiceFactory;
    private final QaDocStateRepository qaDocStateRepository;
    private final PrFileEnrichmentService enrichmentService;
    private final VcsConnectionCredentialsExtractor credentialsExtractor;

    public QaAutoDocListener(ProjectRepository projectRepository,
                              TaskManagementConnectionRepository connectionRepository,
                              TaskManagementClientFactory clientFactory,
                              QaDocGenerationService qaDocGenerationService,
                              CodeAnalysisService codeAnalysisService,
                              VcsClientProvider vcsClientProvider,
                              VcsServiceFactory vcsServiceFactory,
                              QaDocStateRepository qaDocStateRepository,
                              PrFileEnrichmentService enrichmentService,
                              TokenEncryptionService tokenEncryptionService) {
        this.projectRepository = projectRepository;
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

    @Async
    @EventListener
    public void onAnalysisCompleted(AnalysisCompletedEvent event) {
        if (!event.isSuccessful()) {
            log.debug("Skipping QA auto-doc: analysis was not successful (status={})", event.getStatus());
            return;
        }

        Long projectId = event.getProjectId();
        if (projectId == null) {
            return;
        }

        try {
            processQaAutoDoc(event);
        } catch (Exception e) {
            // Non-critical — log and swallow
            log.warn("QA auto-documentation failed for project {} (non-critical): {}",
                    projectId, e.getMessage(), e);
        }
    }

    private void processQaAutoDoc(AnalysisCompletedEvent event) throws Exception {
        // 1. Load project and check config
        Optional<Project> optProject = projectRepository.findById(event.getProjectId());
        if (optProject.isEmpty()) {
            log.debug("QA auto-doc: project {} not found, skipping", event.getProjectId());
            return;
        }

        Project project = optProject.get();
        ProjectConfig config = project.getConfiguration();
        if (config == null || !config.isQaAutoDocEnabled()) {
            log.debug("QA auto-doc: not enabled for project {}", event.getProjectId());
            return;
        }

        QaAutoDocConfig qaConfig = config.getQaAutoDocConfig();
        Long prNumber = event.getPrNumber();
        if (prNumber == null) {
            log.debug("QA auto-doc: no PR number in event, skipping (branch analysis)");
            return;
        }

        // 2. Extract task ID from PR metadata
        Map<String, Object> metrics = event.getMetrics();
        String taskId = extractTaskId(qaConfig, metrics);
        if (taskId == null) {
            log.info("QA auto-doc: no task ID found in PR metadata for project {} PR #{}",
                    event.getProjectId(), prNumber);
            return;
        }

        log.info("QA auto-doc: processing task {} for project {} PR #{}",
                taskId, event.getProjectId(), prNumber);

        // 3. Load server-side QA doc state (replaces insecure Jira comment marker parsing)
        QaDocState state = qaDocStateRepository
                .findByProjectIdAndTaskId(event.getProjectId(), taskId)
                .orElse(null);
        boolean isSamePrRerun = (state != null && state.isPrDocumented(prNumber));

        if (isSamePrRerun) {
            log.info("QA auto-doc: same-PR re-run detected for PR #{} (last commit: {})",
                    prNumber, state.getLastCommitHash());
        } else if (state != null) {
            log.info("QA auto-doc: different PR on same task {} (previously documented PRs: {})",
                    taskId, state.getDocumentedPrNumbers());
        }

        // 4. Load the CodeAnalysis with eagerly-fetched issues for rich prompt context
        CodeAnalysis analysis = null;
        try {
            analysis = codeAnalysisService
                    .getPreviousVersionCodeAnalysis(event.getProjectId(), prNumber)
                    .orElse(null);
            if (analysis != null) {
                log.debug("QA auto-doc: loaded analysis id={} with {} issues",
                        analysis.getId(), analysis.getIssues().size());
            }
        } catch (Exception e) {
            log.warn("QA auto-doc: failed to load analysis for project {} PR #{} — proceeding without: {}",
                    event.getProjectId(), prNumber, e.getMessage());
        }

        // 5. Resolve VCS connection info for diff, enrichment, and credential forwarding
        VcsRepoInfo repoInfo = project.getEffectiveVcsRepoInfo();
        VcsConnection vcsConnection = (repoInfo != null) ? repoInfo.getVcsConnection() : null;
        String workspace = (repoInfo != null) ? repoInfo.getRepoWorkspace() : null;
        String repoSlug = (repoInfo != null) ? repoInfo.getRepoSlug() : null;
        String currentCommitHash = (metrics != null) ? (String) metrics.get("commitHash") : null;
        String sourceBranch = (metrics != null) ? (String) metrics.get("sourceBranch") : null;
        String targetBranch = (metrics != null) ? (String) metrics.get("targetBranch") : null;

        // 5a. Fetch full PR diff
        String diff = null;
        OkHttpClient httpClient = null;
        VcsOperationsService opsService = null;

        if (vcsConnection != null) {
            try {
                httpClient = vcsClientProvider.getHttpClient(vcsConnection);
                opsService = vcsServiceFactory.getOperationsService(vcsConnection.getProviderType());
                diff = opsService.getPullRequestDiff(
                        httpClient, workspace, repoSlug, String.valueOf(prNumber));
                log.info("QA auto-doc: fetched PR diff, size={} chars",
                        diff != null ? diff.length() : 0);
            } catch (Exception e) {
                log.warn("QA auto-doc: failed to fetch PR diff — proceeding without: {}", e.getMessage());
            }
        } else {
            log.warn("QA auto-doc: no VCS connection configured for project {}", project.getId());
        }

        // 5b. Extract changed file paths from diff (needed for enrichment + RAG)
        List<String> changedFilePaths = Collections.emptyList();
        if (diff != null && !diff.isBlank()) {
            try {
                changedFilePaths = DiffParser.extractChangedFiles(diff);
            } catch (Exception e) {
                log.warn("QA auto-doc: failed to extract changed files from diff: {}", e.getMessage());
            }
        }

        // 5c. Compute delta diff for same-PR re-runs (incremental update)
        String deltaDiff = null;
        if (isSamePrRerun && state.getLastCommitHash() != null
                && currentCommitHash != null && opsService != null && httpClient != null) {
            DiffContentFilter contentFilter = new DiffContentFilter();
            final OkHttpClient client = httpClient;
            final VcsOperationsService ops = opsService;
            deltaDiff = VcsDiffUtils.fetchDeltaDiff(
                    (ws, repo, base, head) -> ops.getCommitRangeDiff(client, ws, repo, base, head),
                    workspace, repoSlug,
                    state.getLastCommitHash(), currentCommitHash, contentFilter);
            if (deltaDiff != null) {
                log.info("QA auto-doc: computed delta diff ({} chars) for same-PR re-run", deltaDiff.length());
            }
        }

        // 5d. Fetch enrichment data (file contents + AST metadata + dependency graph)
        PrEnrichmentDataDto enrichmentData = PrEnrichmentDataDto.empty();
        if (vcsConnection != null && !changedFilePaths.isEmpty()) {
            enrichmentData = fetchEnrichmentData(vcsConnection, workspace, repoSlug,
                    currentCommitHash, changedFilePaths);
        }

        // 5e. Extract VCS credentials for Python-side RAG queries
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
                log.warn("QA auto-doc: failed to extract VCS credentials (non-critical): {}", e.getMessage());
            }
        }

        // 6. Resolve task management connection + fetch task details
        TaskManagementConnection connection = connectionRepository
                .findById(qaConfig.taskManagementConnectionId())
                .orElse(null);
        if (connection == null) {
            log.warn("QA auto-doc: task management connection {} not found",
                    qaConfig.taskManagementConnectionId());
            return;
        }

        TaskManagementClient tmClient = createClient(connection);

        TaskDetails taskDetails;
        try {
            taskDetails = tmClient.getTaskDetails(taskId);
        } catch (TaskManagementException e) {
            if (e.getStatusCode() == 404) {
                log.error("QA auto-doc: task {} not found in Jira (HTTP 404) — aborting. " +
                        "Check that the task exists and the API token has access.", taskId);
                return;
            }
            if (e.isAuthError()) {
                log.error("QA auto-doc: authentication failed when fetching task {}: {}",
                        taskId, e.getMessage());
                return;
            }
            log.warn("QA auto-doc: failed to fetch task details for {} — {}", taskId, e.getMessage());
            taskDetails = null;
        } catch (Exception e) {
            log.warn("QA auto-doc: failed to fetch task details for {} — {}", taskId, e.getMessage());
            taskDetails = null;
        }

        // 7. Check for existing Jira comment (READ-ONLY — used only for create-vs-update)
        Optional<TaskComment> existingComment = Optional.empty();
        try {
            existingComment = tmClient.findCommentByMarker(taskId, COMMENT_MARKER_PREFIX);
        } catch (Exception e) {
            log.warn("QA auto-doc: failed to fetch existing comments for task {} — will post new comment: {}",
                    taskId, e.getMessage());
        }

        // 7a. For different-PR accumulation, load previous doc from the existing Jira comment
        String previousDocumentation = null;
        if (!isSamePrRerun && existingComment.isPresent()) {
            previousDocumentation = existingComment.get().body();
            log.info("QA auto-doc: found existing comment for task {} from earlier PR(s) — will merge",
                    taskId);
        }
        // For same-PR re-runs we also pass previous doc so the LLM can produce a targeted delta update
        if (isSamePrRerun && existingComment.isPresent()) {
            previousDocumentation = existingComment.get().body();
        }

        // 8. Build generation context and generate QA documentation
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
                project, event, ctx);

        if (qaDocument == null || qaDocument.isBlank()) {
            log.info("QA auto-doc: LLM determined no documentation needed for task {}", taskId);
            return;
        }

        // Guard: reject error sentinels or too-short output that slipped through
        if (qaDocument.length() < 80 || qaDocument.contains("could not be generated")) {
            log.warn("QA auto-doc: generated document is too short or is an error sentinel (length={}) — not posting to task {}",
                    qaDocument.length(), taskId);
            return;
        }

        // 9. Update server-side state (secure, tamper-proof)
        Long analysisId = (analysis != null) ? analysis.getId() : null;
        upsertQaDocState(project, taskId, currentCommitHash, analysisId, prNumber, state);

        // 10. Post or update Jira comment
        String commentBody = COMMENT_MARKER + "\n\n" + qaDocument;

        try {
            if (existingComment.isPresent()) {
                tmClient.updateComment(taskId, existingComment.get().commentId(), commentBody);
                log.info("QA auto-doc: updated existing comment on task {} (commentId={})",
                        taskId, existingComment.get().commentId());
            } else {
                tmClient.postComment(taskId, commentBody);
                log.info("QA auto-doc: posted new comment on task {}", taskId);
            }
        } catch (Exception e) {
            log.error("QA auto-doc: failed to post/update comment on task {}: {}",
                    taskId, e.getMessage(), e);
        }
    }

    // ── Enrichment helpers ──────────────────────────────────────────

    /**
     * Fetch enrichment data (file contents + AST metadata + dependency graph)
     * with graceful fallback — same pattern as BitbucketAiClientService.
     */
    private PrEnrichmentDataDto fetchEnrichmentData(VcsConnection vcsConnection,
                                                     String workspace,
                                                     String repoSlug,
                                                     String commitHash,
                                                     List<String> changedFiles) {
        VcsClient vcsClient = null;
        try {
            vcsClient = vcsClientProvider.getClient(vcsConnection);
        } catch (Exception e) {
            log.warn("QA auto-doc: failed to obtain VCS client for enrichment (non-critical): {}", e.getMessage());
            return PrEnrichmentDataDto.empty();
        }

        // Primary: full enrichment (AST + file contents + relationships)
        if (enrichmentService != null && enrichmentService.isEnrichmentEnabled()) {
            try {
                PrEnrichmentDataDto data = enrichmentService.enrichPrFiles(
                        vcsClient, workspace, repoSlug, commitHash, changedFiles);
                if (data != null && data.hasData()) {
                    log.info("QA auto-doc: enrichment complete — {} files, {} relationships",
                            data.fileContents() != null ? data.fileContents().size() : 0,
                            data.relationships() != null ? data.relationships().size() : 0);
                    return data;
                }
            } catch (Exception e) {
                log.warn("QA auto-doc: full enrichment failed (non-critical): {}", e.getMessage());
            }

            // Fallback: file contents only (no AST/relationships)
            try {
                PrEnrichmentDataDto fallback = enrichmentService.fetchFileContentsOnly(
                        vcsClient, workspace, repoSlug, commitHash, changedFiles);
                if (fallback != null && fallback.hasData()) {
                    log.info("QA auto-doc: using file-contents-only fallback enrichment");
                    return fallback;
                }
            } catch (Exception e) {
                log.warn("QA auto-doc: file-content fallback enrichment failed (non-critical): {}", e.getMessage());
            }
        }

        return PrEnrichmentDataDto.empty();
    }

    // ── State management ────────────────────────────────────────────

    /**
     * Upsert the QA doc state record after successful generation.
     */
    @Transactional
    protected void upsertQaDocState(Project project, String taskId,
                                     String commitHash, Long analysisId,
                                     Long prNumber, QaDocState existing) {
        try {
            QaDocState state = (existing != null) ? existing : new QaDocState(project, taskId);
            state.recordGeneration(commitHash, analysisId, prNumber);
            qaDocStateRepository.save(state);
            log.debug("QA auto-doc: persisted state for task {} (commit={}, PRs={})",
                    taskId, commitHash, state.getDocumentedPrNumbers());
        } catch (Exception e) {
            // Non-critical — state will be rebuilt on next run
            log.warn("QA auto-doc: failed to persist state for task {}: {}", taskId, e.getMessage());
        }
    }

    /**
     * Extract the task ID from PR metadata using the configured pattern and source.
     */
    String extractTaskId(QaAutoDocConfig config, Map<String, Object> metrics) {
        if (metrics == null) return null;

        String source = switch (config.effectiveTaskIdSource()) {
            case BRANCH_NAME -> {
                String sourceBranch = (String) metrics.get("sourceBranch");
                yield sourceBranch;
            }
            case PR_TITLE -> (String) metrics.get("prTitle");
            case PR_DESCRIPTION -> (String) metrics.get("prDescription");
        };

        if (source == null || source.isBlank()) return null;

        try {
            Pattern pattern = Pattern.compile(config.effectiveTaskIdPattern());
            Matcher matcher = pattern.matcher(source);
            if (matcher.find()) {
                return matcher.group();
            }
        } catch (Exception e) {
            log.warn("QA auto-doc: invalid task ID pattern '{}': {}",
                    config.taskIdPattern(), e.getMessage());
        }
        return null;
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

}
