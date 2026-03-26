package org.rostilos.codecrow.pipelineagent.qadoc;

import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.project.config.QaAutoDocConfig;
import org.rostilos.codecrow.core.model.taskmanagement.TaskManagementConnection;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.core.persistence.repository.taskmanagement.TaskManagementConnectionRepository;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsOperationsService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.rostilos.codecrow.events.analysis.AnalysisCompletedEvent;
import org.rostilos.codecrow.taskmanagement.TaskManagementClient;
import org.rostilos.codecrow.taskmanagement.TaskManagementClientFactory;
import org.rostilos.codecrow.taskmanagement.ETaskManagementPlatform;
import org.rostilos.codecrow.taskmanagement.model.TaskComment;
import org.rostilos.codecrow.taskmanagement.model.TaskDetails;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.event.EventListener;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestTemplate;

import okhttp3.OkHttpClient;

import java.util.*;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Listens for {@link AnalysisCompletedEvent} and triggers QA auto-documentation
 * generation when the feature is enabled for the project.
 * <p>
 * This is a <strong>non-blocking, non-critical</strong> post-processing step.
 * Failures here never block or affect the PR review result.
 * </p>
 *
 * <h3>Flow:</h3>
 * <ol>
 *   <li>Receive analysis completed event</li>
 *   <li>Load project config, check if QA auto-doc is enabled</li>
 *   <li>Extract task ID from PR metadata using configured regex + source</li>
 *   <li>Fetch task details from Jira (for context)</li>
 *   <li>Call inference-orchestrator /qa-documentation endpoint</li>
 *   <li>Post/update comment on the Jira task</li>
 * </ol>
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

    public QaAutoDocListener(ProjectRepository projectRepository,
                              TaskManagementConnectionRepository connectionRepository,
                              TaskManagementClientFactory clientFactory,
                              QaDocGenerationService qaDocGenerationService,
                              CodeAnalysisService codeAnalysisService,
                              VcsClientProvider vcsClientProvider,
                              VcsServiceFactory vcsServiceFactory) {
        this.projectRepository = projectRepository;
        this.connectionRepository = connectionRepository;
        this.clientFactory = clientFactory;
        this.qaDocGenerationService = qaDocGenerationService;
        this.codeAnalysisService = codeAnalysisService;
        this.vcsClientProvider = vcsClientProvider;
        this.vcsServiceFactory = vcsServiceFactory;
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

        // 3. Load the CodeAnalysis with eagerly-fetched issues for rich prompt context
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

        // 3a. Fetch the raw PR diff from the VCS platform — critical context for QA
        String diff = null;
        try {
            VcsRepoInfo repoInfo = project.getEffectiveVcsRepoInfo();
            if (repoInfo != null && repoInfo.getVcsConnection() != null) {
                VcsConnection vcsConnection = repoInfo.getVcsConnection();
                OkHttpClient httpClient = vcsClientProvider.getHttpClient(vcsConnection);
                VcsOperationsService opsService = vcsServiceFactory.getOperationsService(
                        vcsConnection.getProviderType());
                diff = opsService.getPullRequestDiff(
                        httpClient, repoInfo.getRepoWorkspace(),
                        repoInfo.getRepoSlug(), String.valueOf(prNumber));
                log.info("QA auto-doc: fetched PR diff, size={} chars",
                        diff != null ? diff.length() : 0);
            } else {
                log.warn("QA auto-doc: no VCS connection configured for project {}", project.getId());
            }
        } catch (Exception e) {
            log.warn("QA auto-doc: failed to fetch PR diff for project {} PR #{} — proceeding without: {}",
                    event.getProjectId(), prNumber, e.getMessage());
        }

        // 4. Resolve task management connection
        TaskManagementConnection connection = connectionRepository
                .findById(qaConfig.taskManagementConnectionId())
                .orElse(null);
        if (connection == null) {
            log.warn("QA auto-doc: task management connection {} not found",
                    qaConfig.taskManagementConnectionId());
            return;
        }

        TaskManagementClient client = createClient(connection);

        // 4. Fetch task details for context
        TaskDetails taskDetails;
        try {
            taskDetails = client.getTaskDetails(taskId);
        } catch (Exception e) {
            log.warn("QA auto-doc: failed to fetch task details for {} — {}", taskId, e.getMessage());
            taskDetails = null;
        }

        // 5. Check for existing QA doc comment on this task (for multi-PR accumulation)
        Optional<TaskComment> existingComment = client.findCommentByMarker(taskId, COMMENT_MARKER_PREFIX);
        String previousDocumentation = null;
        boolean isSamePrRerun = false;

        if (existingComment.isPresent()) {
            String existingBody = existingComment.get().body();
            // Detect if the current PR is already documented (same-PR re-analysis → overwrite)
            isSamePrRerun = isCurrentPrAlreadyDocumented(existingBody, event.getPrNumber());

            if (!isSamePrRerun) {
                // Different PR on the same task → pass previous doc to LLM for merging
                previousDocumentation = existingBody;
                log.info("QA auto-doc: found existing comment for task {} from earlier PR(s) — will merge (commentId={})",
                        taskId, existingComment.get().commentId());
            } else {
                log.info("QA auto-doc: re-analysis of same PR #{} — will overwrite existing comment",
                        event.getPrNumber());
            }
        }

        // 6. Generate QA documentation via inference orchestrator
        String qaDocument = qaDocGenerationService.generateQaDocumentation(
                project, event, qaConfig, taskDetails, analysis, diff, previousDocumentation);

        if (qaDocument == null || qaDocument.isBlank()) {
            log.info("QA auto-doc: LLM determined no documentation needed for task {}", taskId);
            return;
        }

        // 7. Prepend marker for detection
        String commentBody = COMMENT_MARKER + "\n\n" + qaDocument;

        // 8. Post or update comment
        if (existingComment.isPresent()) {
            client.updateComment(taskId, existingComment.get().commentId(), commentBody);
            log.info("QA auto-doc: updated existing comment on task {} (commentId={})",
                    taskId, existingComment.get().commentId());
        } else {
            client.postComment(taskId, commentBody);
            log.info("QA auto-doc: posted new comment on task {}", taskId);
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

    /**
     * Check if the current PR is already documented in an existing QA doc comment.
     * <p>
     * Looks for the PR-tracking marker {@code <!-- codecrow-qa-autodoc:prs=42,57 -->}.
     * If the current PR number appears in the list, this is a re-analysis of the same PR
     * (e.g., new commits pushed) and we should overwrite rather than merge.
     *
     * @param commentBody   the existing comment body (plain text extracted from ADF)
     * @param currentPrNumber the PR number being analyzed now
     * @return true if the current PR is already listed in the marker
     */
    public static boolean isCurrentPrAlreadyDocumented(String commentBody, Long currentPrNumber) {
        if (commentBody == null || currentPrNumber == null) return false;
        // Match the PR-tracking marker: <!-- codecrow-qa-autodoc:prs=42,57 -->
        Pattern prMarkerPattern = Pattern.compile("<!-- codecrow-qa-autodoc:prs=([\\d,]+) -->");
        Matcher matcher = prMarkerPattern.matcher(commentBody);
        if (matcher.find()) {
            String prList = matcher.group(1);
            for (String pr : prList.split(",")) {
                try {
                    if (Long.parseLong(pr.trim()) == currentPrNumber) {
                        return true;
                    }
                } catch (NumberFormatException ignored) {
                    // skip malformed entries
                }
            }
        }
        return false;
    }
}
