package org.rostilos.codecrow.analysisengine.processor.analysis;

import org.rostilos.codecrow.analysisengine.util.ProjectVcsInfoRetriever;
import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.pullrequest.PullRequest;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.core.service.TaskImplementationEvidenceService;
import org.rostilos.codecrow.filecontent.service.FileSnapshotService;
import org.rostilos.codecrow.analysisengine.service.pr.PrIssueTrackingService;
import org.rostilos.codecrow.analysisengine.service.AstScopeEnricher;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequestImpl;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.FileContentDto;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.PrEnrichmentDataDto;
import org.rostilos.codecrow.analysisengine.exception.AnalysisLockedException;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.analysisengine.service.PullRequestService;
import org.rostilos.codecrow.analysisapi.rag.RagOperationsService;
import org.rostilos.codecrow.commitgraph.service.AnalyzedCommitService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsAiClientService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsReportingService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.analysisengine.aiclient.AiAnalysisClient;
import org.rostilos.codecrow.events.analysis.AnalysisStartedEvent;
import org.rostilos.codecrow.events.analysis.AnalysisCompletedEvent;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.security.GeneralSecurityException;
import java.time.Duration;
import java.time.Instant;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Collections;
import java.util.TreeMap;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.stream.Collectors;

import org.rostilos.codecrow.analysisengine.util.DiffFingerprintUtil;
import org.rostilos.codecrow.analysisengine.util.PromptDryRunMode;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;

/**
 * Generic service that handles pull request analysis.
 * Uses VCS-specific services via VcsServiceFactory for provider-specific
 * operations.
 */
@Service
public class PullRequestAnalysisProcessor {
    private static final Logger log = LoggerFactory.getLogger(PullRequestAnalysisProcessor.class);

    private final CodeAnalysisService codeAnalysisService;
    private final TaskImplementationEvidenceService taskImplementationEvidenceService;
    private final PullRequestService pullRequestService;
    private final AiAnalysisClient aiAnalysisClient;
    private final VcsServiceFactory vcsServiceFactory;
    private final AnalysisLockService analysisLockService;
    private final RagOperationsService ragOperationsService;
    private final ApplicationEventPublisher eventPublisher;
    private final AnalyzedCommitService analyzedCommitService;
    private final VcsClientProvider vcsClientProvider;
    private final FileSnapshotService fileSnapshotService;
    private final PrIssueTrackingService prIssueTrackingService;
    private final AstScopeEnricher astScopeEnricher;

    public PullRequestAnalysisProcessor(
            PullRequestService pullRequestService,
            CodeAnalysisService codeAnalysisService,
            TaskImplementationEvidenceService taskImplementationEvidenceService,
            AiAnalysisClient aiAnalysisClient,
            VcsServiceFactory vcsServiceFactory,
            AnalysisLockService analysisLockService,
            AnalyzedCommitService analyzedCommitService,
            VcsClientProvider vcsClientProvider,
            FileSnapshotService fileSnapshotService,
            PrIssueTrackingService prIssueTrackingService,
            AstScopeEnricher astScopeEnricher,
            @Autowired(required = false) RagOperationsService ragOperationsService,
            @Autowired(required = false) ApplicationEventPublisher eventPublisher
    ) {
        this.codeAnalysisService = codeAnalysisService;
        this.taskImplementationEvidenceService = taskImplementationEvidenceService;
        this.pullRequestService = pullRequestService;
        this.aiAnalysisClient = aiAnalysisClient;
        this.vcsServiceFactory = vcsServiceFactory;
        this.analysisLockService = analysisLockService;
        this.ragOperationsService = ragOperationsService;
        this.eventPublisher = eventPublisher;
        this.analyzedCommitService = analyzedCommitService;
        this.vcsClientProvider = vcsClientProvider;
        this.fileSnapshotService = fileSnapshotService;
        this.prIssueTrackingService = prIssueTrackingService;
        this.astScopeEnricher = astScopeEnricher;
    }

    public interface EventConsumer {
        void accept(Map<String, Object> event);
    }

    public Map<String, Object> process(
            PrProcessRequest request,
            EventConsumer consumer,
            Project project
    ) throws GeneralSecurityException {
        Instant startTime = Instant.now();
        String correlationId = java.util.UUID.randomUUID().toString();

        // Publish analysis started event
        publishAnalysisStartedEvent(project, request, correlationId);

        // Check if a lock was already acquired by the caller (e.g., webhook handler)
        // to prevent double-locking which causes unnecessary 2-minute waits
        String lockKey;
        boolean isPreAcquired = false;
        if (request.getPreAcquiredLockKey() != null && !request.getPreAcquiredLockKey().isBlank()) {
            lockKey = request.getPreAcquiredLockKey();
            isPreAcquired = true;
            log.info("Using pre-acquired lock: {} for project={}, PR={}", lockKey, project.getId(),
                    request.getPullRequestId());
        } else {
            Optional<String> acquiredLock = analysisLockService.acquireLockWithWait(
                    project,
                    request.getSourceBranchName(),
                    AnalysisLockType.PR_ANALYSIS,
                    request.getCommitHash(),
                    request.getPullRequestId(),
                    consumer::accept);

            if (acquiredLock.isEmpty()) {
                String message = String.format(
                        "Failed to acquire lock after %d minutes for project=%s, PR=%d, branch=%s. Another analysis is still in progress.",
                        analysisLockService.getLockWaitTimeoutMinutes(),
                        project.getId(),
                        request.getPullRequestId(),
                        request.getSourceBranchName());
                log.warn(message);

                // Publish failed event due to lock timeout
                publishAnalysisCompletedEvent(project, request, correlationId, startTime,
                        AnalysisCompletedEvent.CompletionStatus.FAILED, 0, 0, "Lock acquisition timeout");

                throw new AnalysisLockedException(
                        AnalysisLockType.PR_ANALYSIS.name(),
                        request.getSourceBranchName(),
                        project.getId());
            }
            lockKey = acquiredLock.get();
        }

        try {
            EVcsProvider provider = ProjectVcsInfoRetriever.getVcsProvider(project);
            VcsReportingService reportingService = vcsServiceFactory.getReportingService(provider);
            // Get all previous analyses for this PR to provide full issue history to AI
            List<CodeAnalysis> allPrAnalyses = codeAnalysisService.getAllPrAnalyses(
                    project.getId(),
                    request.getPullRequestId());

            // Get the most recent analysis for incremental diff calculation
            Optional<CodeAnalysis> previousAnalysis = allPrAnalyses.isEmpty()
                    ? Optional.empty()
                    : Optional.of(allPrAnalyses.get(0));

            VcsAiClientService aiClientService = vcsServiceFactory.getAiClientService(provider);
            List<AiAnalysisRequest> aiRequests = aiClientService.buildAiAnalysisRequests(
                    project, request, previousAnalysis, allPrAnalyses);
            // Request construction acquires and verifies the provider's immutable
            // full head object ID. Persist the PR only after that canonicalization,
            // never from an abbreviated or otherwise unverified webhook value.
            PullRequest pullRequest = pullRequestService.createOrUpdatePullRequest(
                    request.getProjectId(),
                    request.getPullRequestId(),
                    request.getCommitHash(),
                    request.getSourceBranchName(),
                    request.getTargetBranchName(),
                    project);

            if (aiRequests == null || aiRequests.isEmpty()) {
                String message = "No changed files match the project analysis scope";
                log.info("Skipping PR analysis for project={}, PR={}: {}",
                        project.getId(), request.getPullRequestId(), message);
                consumer.accept(Map.of("type", "info", "message", message));
                publishAnalysisCompletedEvent(project, request, correlationId, startTime,
                        AnalysisCompletedEvent.CompletionStatus.SUCCESS, 0, 0, null);
                return Map.of("status", "ignored", "message", message);
            }

            AiAnalysisRequest aiRequest = aiRequests.get(0);
            String diffFingerprint = computeReviewIdentity(aiRequest);
            boolean promptDryRun = PromptDryRunMode.isEnabledForProject(project.getId());

            if (!promptDryRun) {
                CacheHitType cacheHit = postAnalysisCacheIfExist(
                        project, pullRequest, request.getCommitHash(), request.getPullRequestId(),
                        reportingService, request.getPlaceholderCommentId(), request.getTargetBranchName(),
                        request.getSourceBranchName(), diffFingerprint);
                if (cacheHit != CacheHitType.NONE) {
                    publishAnalysisCompletedEvent(project, request, correlationId, startTime,
                            AnalysisCompletedEvent.CompletionStatus.SUCCESS, 0, 0, null);
                    String cacheStatus = cacheHit == CacheHitType.COMMIT_HASH ? "cached_by_commit" : "cached";
                    return Map.of("status", cacheStatus, "cached", true);
                }

                if (postDiffFingerprintCacheIfExist(
                        request, diffFingerprint, project, pullRequest, aiRequest, reportingService
                )) {
                    publishAnalysisCompletedEvent(project, request, correlationId, startTime,
                            AnalysisCompletedEvent.CompletionStatus.SUCCESS, 0, 0, null);
                    return Map.of("status", "cached_by_fingerprint", "cached", true);
                }
            } else {
                log.warn(
                        "Prompt dry run bypassing analysis caches for project={}, PR={}",
                        project.getId(), request.getPullRequestId());
            }

            // Only prepare RAG after the exact snapshot/configuration cache has missed.
            ensureRagIndexForTargetBranch(project, request.getTargetBranchName(), consumer);

            AtomicBoolean lockLeaseLost = new AtomicBoolean(false);
            Map<String, Object> aiResponse = aiAnalysisClient.performAnalysis(aiRequest, event -> {
                if ("processing".equals(String.valueOf(event.get("state")))) {
                    try {
                        if (!analysisLockService.renewLock(
                                lockKey,
                                analysisLockService.getLeaseMinutes(AnalysisLockType.PR_ANALYSIS))) {
                            lockLeaseLost.set(true);
                            log.error("PR analysis lost its lock lease: {}", lockKey);
                        }
                    } catch (Exception leaseError) {
                        lockLeaseLost.set(true);
                        log.error("Failed to renew PR analysis lock lease: {}", lockKey, leaseError);
                    }
                }
                try {
                    log.debug("Received event from AI client: type={}", event.get("type"));
                    consumer.accept(event);
                    log.debug("Event forwarded to consumer successfully");
                } catch (Exception ex) {
                    log.error("Event consumer failed: {}", ex.getMessage(), ex);
                }
            });
            if (lockLeaseLost.get()) {
                throw new IOException(
                        "PR analysis lost its lock lease while the review worker was active");
            }

            if (AiAnalysisClient.isPromptDryRunResult(aiResponse)) {
                Object artifact = aiResponse.get("promptArtifact");
                log.warn(
                        "Prompt dry run completed for project={}, PR={}; artifact={}",
                        project.getId(), request.getPullRequestId(), artifact);
                consumer.accept(Map.of(
                        "type", "info",
                        "state", "prompt_dry_run_completed",
                        "message", "Prompt dry run completed without publishing an analysis",
                        "promptArtifact", artifact != null ? artifact : Map.of()));
                return aiResponse;
            }

            // === Extract file contents from enrichment data for line hash computation ===
            Map<String, String> fileContents = new java.util.HashMap<>(extractFileContents(aiRequest));
            java.util.Set<String> allChangedFiles = new java.util.HashSet<>(aiRequest.getChangedFiles());

            // === VCS fallback: when enrichment data is empty (disabled, failed, or
            // provider-specific),
            // fetch file contents directly from VCS to ensure source viewer always has data
            // ===
            if (fileContents.isEmpty()) {
                log.info(
                        "Enrichment file contents empty — falling back to direct VCS file fetch for PR {} (project={})",
                        request.getPullRequestId(), project.getId());
                fileContents = fetchFileContentsFromVcs(project, new java.util.ArrayList<>(allChangedFiles),
                        request.getCommitHash());
            }

            CodeAnalysis newAnalysis = codeAnalysisService.createAnalysisFromAiResponse(
                    project,
                    aiResponse,
                    request.getPullRequestId(),
                    request.getTargetBranchName(),
                    request.getSourceBranchName(),
                    request.getCommitHash(),
                    request.getPrAuthorId(),
                    request.getPrAuthorUsername(),
                    diffFingerprint,
                    fileContents,
                    taskContextValue(aiRequest, "task_key", "taskKey", "key"),
                    taskContextValue(aiRequest, "task_summary", "taskSummary", "summary"));

            persistTaskImplementationEvidence(newAnalysis, aiResponse.get("taskEvidence"));

            int issuesFound = newAnalysis.getTotalIssues();

            // === AST scope enrichment: resolve scope boundaries for each issue ===
            try {
                if (newAnalysis.getIssues() != null && !newAnalysis.getIssues().isEmpty()) {
                    astScopeEnricher.enrichWithAstScopes(newAnalysis.getIssues(), fileContents);
                }
            } catch (Exception astEx) {
                log.warn("AST scope enrichment failed (non-critical): {}", astEx.getMessage());
            }

            // === Persist file snapshots at PR level for the source code viewer ===
            // Accumulates across iterations: 2nd run adds new files, keeps old ones.
            try {
                fileSnapshotService.persistSnapshotsForPr(pullRequest, newAnalysis, fileContents,
                        request.getCommitHash());
            } catch (Exception snapEx) {
                log.warn("Failed to persist file snapshots (non-critical): {}", snapEx.getMessage());
            }

            // === Deterministic PR issue tracking against previous iteration ===
            try {
                if (previousAnalysis.isPresent()) {
                    CodeAnalysis previous = previousAnalysis.get();
                    boolean refreshedSameRecord = newAnalysis == previous
                            || (newAnalysis.getId() != null && newAnalysis.getId().equals(previous.getId()));
                    if (refreshedSameRecord) {
                        log.debug("Skipping PR iteration tracking for refreshed analysis record {}",
                                newAnalysis.getId());
                    } else {
                        Map<String, String> prevFileContents = fileSnapshotService.getFileContentsMap(
                                previous.getId());
                        prIssueTrackingService.trackPrIteration(
                                newAnalysis, previous, fileContents, prevFileContents);
                    }
                }
            } catch (Exception trackEx) {
                log.warn("PR issue tracking failed (non-critical): {}", trackEx.getMessage());
            }

            try {
                reportingService.postAnalysisResults(
                        newAnalysis,
                        project,
                        request.getPullRequestId(),
                        pullRequest.getId(),
                        request.getPlaceholderCommentId());
            } catch (IOException e) {
                log.error("Failed to post analysis results to VCS: {}", e.getMessage(), e);
                consumer.accept(Map.of(
                        "type", "warning",
                        "message", "Analysis completed but failed to post results to VCS: " + e.getMessage()));
            }

            // === DAG: Mark PR commits as ANALYZED ===
            markPrCommitsAnalyzed(project, request.getSourceBranchName(), request.getCommitHash(), newAnalysis);

            // Publish successful completion event
            publishAnalysisCompletedEvent(project, request, correlationId, startTime,
                    AnalysisCompletedEvent.CompletionStatus.SUCCESS, issuesFound,
                    allChangedFiles != null ? allChangedFiles.size() : 0, null);

            return aiResponse;
        } catch (IOException e) {
            log.error("IOException during PR analysis: {}", e.getMessage(), e);
            consumer.accept(Map.of(
                    "type", "error",
                    "message", "Analysis failed due to I/O error: " + e.getMessage()));

            // Publish failed event
            publishAnalysisCompletedEvent(project, request, correlationId, startTime,
                    AnalysisCompletedEvent.CompletionStatus.FAILED, 0, 0, e.getMessage());

            return Map.of("status", "error", "message", e.getMessage());
        } finally {
            if (!isPreAcquired) {
                analysisLockService.releaseLock(lockKey);
            }
        }
    }

    private String taskContextValue(AiAnalysisRequest aiRequest, String... keys) {
        Map<String, String> taskContext = aiRequest.getTaskContext();
        if (taskContext == null || taskContext.isEmpty()) {
            return null;
        }
        for (String key : keys) {
            String value = taskContext.get(key);
            if (value != null && !value.isBlank()) {
                return value.trim();
            }
        }
        return null;
    }

    /**
     * Extract file contents from the AI analysis request's enrichment data.
     * Returns a map of filePath → raw file content suitable for line hash
     * computation.
     */
    private Map<String, String> extractFileContents(AiAnalysisRequest aiRequest) {
        if (!(aiRequest instanceof AiAnalysisRequestImpl impl)) {
            return Collections.emptyMap();
        }
        PrEnrichmentDataDto enrichment = impl.getEnrichmentData();
        if (enrichment == null || enrichment.fileContents() == null) {
            return Collections.emptyMap();
        }
        Map<String, String> result = enrichment.fileContents().stream()
                .filter(f -> !f.skipped() && f.content() != null)
                .collect(Collectors.toMap(
                        FileContentDto::path,
                        FileContentDto::content,
                        (a, b) -> a // in case of duplicates, keep first
                ));
        log.debug("Extracted {} file contents from enrichment data for line hash computation", result.size());
        return result;
    }

    /**
     * Fetch file contents directly from VCS when enrichment data is empty.
     * This is the fallback path that ensures file snapshots are always available
     * for the source code viewer, regardless of enrichment status.
     *
     * @param project      the project with VCS connection info
     * @param changedFiles list of file paths to fetch
     * @param commitHash   the commit to fetch files from
     * @return map of filePath → raw content (empty map on failure)
     */
    private Map<String, String> fetchFileContentsFromVcs(Project project, List<String> changedFiles,
                                                         String commitHash) {
        if (changedFiles == null || changedFiles.isEmpty()) {
            return Collections.emptyMap();
        }
        try {
            VcsRepoInfo repoInfo = project.getEffectiveVcsRepoInfo();
            if (repoInfo == null || repoInfo.getVcsConnection() == null) {
                log.warn("No VCS repo info available — cannot fetch file contents for source viewer");
                return Collections.emptyMap();
            }
            VcsClient vcsClient = vcsClientProvider.getClient(repoInfo.getVcsConnection());
            Map<String, String> contents = vcsClient.getFileContents(
                    repoInfo.getRepoWorkspace(),
                    repoInfo.getRepoSlug(),
                    changedFiles,
                    commitHash,
                    100_000 // 100 KB max per file, consistent with enrichment service
            );
            log.info("VCS fallback: fetched {}/{} file contents for source viewer (commit={})",
                    contents.size(), changedFiles.size(),
                    commitHash != null ? commitHash.substring(0, Math.min(7, commitHash.length())) : "null");
            return contents;
        } catch (Exception e) {
            log.warn("VCS fallback file fetch failed (non-critical): {}", e.getMessage());
            return Collections.emptyMap();
        }
    }

    /**
     * Ensure PR-level file snapshots exist after a cache-hit clone.
     * <p>
     * Strategy:
     * <ol>
     * <li>Copy PR-level snapshots from the source analysis's original PR (fast, no
     * VCS calls)</li>
     * <li>If source PR has no snapshots, fetch from VCS using the provided
     * changed-file list
     * or, as a last resort, file paths extracted from the cloned analysis's
     * issues</li>
     * </ol>
     *
     * @param pullRequest    the current PR to persist snapshots for
     * @param cloned         the cloned analysis
     * @param sourceAnalysis the original (cache-hit) analysis
     * @param project        the project
     * @param commitHash     the commit hash for VCS fallback
     * @param changedFiles   explicit changed-file list (may be null for commit-hash
     *                       cache)
     */
    private void persistPrSnapshotsForCacheHit(PullRequest pullRequest, CodeAnalysis cloned,
                                               CodeAnalysis sourceAnalysis, Project project,
                                               String commitHash, List<String> changedFiles) {
        try {
            // Strategy 1: Copy PR-level snapshots from the source analysis's original PR
            if (sourceAnalysis.getPrNumber() != null) {
                Optional<PullRequest> sourcePr = pullRequestService.findPullRequest(
                        project.getId(), sourceAnalysis.getPrNumber());
                if (sourcePr.isPresent()) {
                    Map<String, String> sourceContents = fileSnapshotService.getFileContentsMapForPr(
                            sourcePr.get().getId());
                    if (!sourceContents.isEmpty()) {
                        fileSnapshotService.persistSnapshotsForPr(pullRequest, cloned, sourceContents, commitHash);
                        log.info("Copied {} PR snapshots from source PR {} to PR {} (cache hit)",
                                sourceContents.size(), sourceAnalysis.getPrNumber(), pullRequest.getPrNumber());
                        return;
                    }
                }
            }

            // Strategy 2: Fetch from VCS using explicit file list or issue file paths
            List<String> filePaths = changedFiles;
            if (filePaths == null || filePaths.isEmpty()) {
                filePaths = cloned.getIssues().stream()
                        .map(CodeAnalysisIssue::getFilePath)
                        .filter(fp -> fp != null && !fp.isBlank())
                        .distinct()
                        .collect(Collectors.toList());
            }
            if (!filePaths.isEmpty()) {
                Map<String, String> fileContents = fetchFileContentsFromVcs(project, filePaths, commitHash);
                if (!fileContents.isEmpty()) {
                    fileSnapshotService.persistSnapshotsForPr(pullRequest, cloned, fileContents, commitHash);
                }
            }
        } catch (Exception e) {
            log.warn("Failed to persist PR snapshots for cache hit (non-critical): {}", e.getMessage());
        }
    }

    protected boolean postDiffFingerprintCacheIfExist(
            PrProcessRequest request,
            String diffFingerprint,
            Project project,
            PullRequest pullRequest,
            AiAnalysisRequest aiRequest,
            VcsReportingService reportingService

    ) {
        // Get analysis cache by diff fingerprint (any PR ID) - less ideal than commit hash but still a win
        if(diffFingerprint == null) {
            return false;
        }
        Optional<CodeAnalysis> fingerprintHit = codeAnalysisService.getAnalysisByDiffFingerprint(
                project.getId(), diffFingerprint);
        if(!fingerprintHit.isPresent()) {
            return false;
        }
        log.info(
                "Diff fingerprint cache hit for project={}, fingerprint={} (source PR={}). Cloning for PR={}.",
                project.getId(), diffFingerprint.substring(0, 8) + "...",
                fingerprintHit.get().getPrNumber(), request.getPullRequestId());
        CodeAnalysis cloned = codeAnalysisService.cloneAnalysisForPr(
                fingerprintHit.get(), project, request.getPullRequestId(),
                request.getCommitHash(), request.getTargetBranchName(),
                request.getSourceBranchName(), diffFingerprint);
        copyTaskImplementationEvidence(fingerprintHit.get(), cloned);
        // Persist PR-level snapshots for the source code viewer
        persistPrSnapshotsForCacheHit(pullRequest, cloned, fingerprintHit.get(), project,
                request.getCommitHash(), aiRequest.getChangedFiles());
        try {
            reportingService.postAnalysisResults(cloned, project,
                    request.getPullRequestId(), pullRequest.getId(),
                    request.getPlaceholderCommentId());
        } catch (IOException e) {
            log.error("Failed to post fingerprint-cached results to VCS: {}", e.getMessage(), e);
        }
        return true;
    }

    /** Describes which cache layer produced a hit. */
    protected enum CacheHitType { NONE, EXACT, COMMIT_HASH }

    protected CacheHitType postAnalysisCacheIfExist(
            Project project,
            PullRequest pullRequest,
            String commitHash,
            Long prId,
            VcsReportingService reportingService,
            String placeholderCommentId,
            String targetBranch,
            String sourceBranch,
            String expectedReviewIdentity
    ) {
        Optional<CodeAnalysis> cachedAnalysis = codeAnalysisService.getCodeAnalysisCache(
                project.getId(),
                commitHash,
                prId);

        // Get analysis cache by PR ID and commit hash
        if (cachedAnalysis.isPresent()
                && expectedReviewIdentity != null
                && expectedReviewIdentity.equals(cachedAnalysis.get().getDiffFingerprint())) {
            try {
                reportingService.postAnalysisResults(cachedAnalysis.get(),
                        project,
                        prId,
                        pullRequest.getId(),
                        placeholderCommentId);
            } catch (IOException e) {
                log.error("Failed to post cached analysis results to VCS: {}", e.getMessage(), e);
            }
            return CacheHitType.EXACT;
        }

        // Get analysis cache by commit hash (any PR ID)
        Optional<CodeAnalysis> commitHashHit = codeAnalysisService.getAnalysisByCommitHash(
                project.getId(), commitHash);
        if (commitHashHit.isPresent()
                && expectedReviewIdentity != null
                && expectedReviewIdentity.equals(commitHashHit.get().getDiffFingerprint())) {
            log.info("Commit-hash cache hit for project={}, commit={} (source PR={}). Cloning for PR={}.",
                    project.getId(), commitHash,
                    commitHashHit.get().getPrNumber(), prId
            );
            CodeAnalysis cloned = codeAnalysisService.cloneAnalysisForPr(
                    commitHashHit.get(), project, prId,
                    commitHash, targetBranch,
                    sourceBranch, commitHashHit.get().getDiffFingerprint());
            copyTaskImplementationEvidence(commitHashHit.get(), cloned);
            // Persist PR-level snapshots for the source code viewer
            persistPrSnapshotsForCacheHit(pullRequest, cloned, commitHashHit.get(), project,
                    commitHash, null);
            try {
                reportingService.postAnalysisResults(
                        cloned,
                        project,
                        prId,
                        pullRequest.getId(),
                        placeholderCommentId
                );
            } catch (IOException e) {
                log.error("Failed to post commit-hash cached results to VCS: {}", e.getMessage(), e);
            }
            return CacheHitType.COMMIT_HASH;
        }
        return CacheHitType.NONE;
    }

    private void persistTaskImplementationEvidence(
            CodeAnalysis analysis,
            Object rawTaskEvidence) {
        try {
            TaskImplementationEvidenceService.PersistenceResult result =
                    taskImplementationEvidenceService.persistFromAnalysisResponse(
                            analysis, rawTaskEvidence);
            if (result.persisted() > 0 || result.rejected() > 0
                    || result.duplicate() > 0) {
                log.info(
                        "Task implementation evidence for analysis {}: persisted={}, rejected={}, duplicate={}",
                        analysis.getId(),
                        result.persisted(),
                        result.rejected(),
                        result.duplicate());
            }
        } catch (RuntimeException e) {
            log.warn(
                    "Task implementation evidence persistence failed for analysis {}; "
                            + "continuing review publication without auxiliary evidence: {}",
                    analysis != null ? analysis.getId() : null,
                    e.getMessage());
        }
    }

    private void copyTaskImplementationEvidence(
            CodeAnalysis source,
            CodeAnalysis target) {
        try {
            TaskImplementationEvidenceService.PersistenceResult result =
                    taskImplementationEvidenceService.copyForAnalysis(source, target);
            if (result.persisted() > 0) {
                log.info(
                        "Copied {} task implementation evidence record(s) from analysis {} to {}",
                        result.persisted(),
                        source.getId(),
                        target.getId());
            }
        } catch (RuntimeException e) {
            log.warn(
                    "Task implementation evidence cache copy failed for analysis {} -> {}; "
                            + "continuing with cached review output: {}",
                    source != null ? source.getId() : null,
                    target != null ? target.getId() : null,
                    e.getMessage());
        }
    }

    private Map<String, String> reviewIdentityInputs(AiAnalysisRequest request) {
        TreeMap<String, String> inputs = new TreeMap<>();
        putIdentity(inputs, "baseCommit", request.getBaseCommitHash());
        putIdentity(inputs, "headCommit", request.getCurrentCommitHash());
        putIdentity(inputs, "previousCommit", request.getPreviousCommitHash());
        putIdentity(inputs, "targetBranch", request.getTargetBranchName());
        putIdentity(inputs, "sourceBranch", request.getSourceBranchName());
        putIdentity(inputs, "provider", request.getAiProvider());
        putIdentity(inputs, "model", request.getAiModel());
        putIdentity(inputs, "baseUrl", request.getAiBaseUrl());
        putIdentity(inputs, "customParameters", request.getAiCustomParameters());
        putIdentity(inputs, "maxTokens", request.getMaxAllowedTokens());
        putIdentity(inputs, "useLocalMcp", request.getUseLocalMcp());
        putIdentity(inputs, "useMcpTools", request.getUseMcpTools());
        putIdentity(inputs, "ragEnabled", request.getRagEnabled());
        putIdentity(inputs, "analysisType", request.getAnalysisType());
        putIdentity(inputs, "analysisMode", request.getAnalysisMode());
        putIdentity(inputs, "projectRules", request.getProjectRules());
        putIdentity(inputs, "taskHistory", request.getTaskHistoryContext());
        if (request.getProjectCapabilities() != null) {
            putIdentity(inputs, "pluginSelection", request.getProjectCapabilities().fingerprint());
        }
        if (request.getTaskContext() != null) {
            request.getTaskContext().entrySet().stream()
                    .sorted(Map.Entry.comparingByKey())
                    .forEach(entry -> putIdentity(
                            inputs, "taskContext:" + entry.getKey(), entry.getValue()));
        }
        List<String> previousIssues = request.getPreviousCodeAnalysisIssues() == null
                ? List.of()
                : request.getPreviousCodeAnalysisIssues().stream()
                        .map(String::valueOf)
                        .sorted()
                        .toList();
        for (int index = 0; index < previousIssues.size(); index++) {
            putIdentity(inputs, "previousIssue:" + index, previousIssues.get(index));
        }
        putIdentity(inputs, "changedFiles", sortedValues(request.getChangedFiles()));
        putIdentity(inputs, "deletedFiles", sortedValues(request.getDeletedFiles()));
        return inputs;
    }

    protected String computeReviewIdentity(AiAnalysisRequest request) {
        return DiffFingerprintUtil.compute(request.getRawDiff(), reviewIdentityInputs(request));
    }

    private static String sortedValues(List<String> values) {
        return values == null ? "" : values.stream().sorted().collect(Collectors.joining("\n"));
    }

    private static void putIdentity(Map<String, String> target, String key, Object value) {
        target.put(key, value == null ? "" : String.valueOf(value));
    }

    /**
     * After successful PR analysis, record the source branch HEAD commit
     * as analyzed in the analyzed_commit table.
     *
     * @param project      the project
     * @param sourceBranch the PR source branch (where the commits live)
     * @param commitHash   the HEAD commit of the source branch
     * @param analysis     the CodeAnalysis to link, or null for cache-hit scenarios
     */
    private void markPrCommitsAnalyzed(Project project, String sourceBranch, String commitHash, CodeAnalysis analysis) {
        try {
            if (commitHash == null) return;

            // Record the PR's HEAD commit as analyzed
            analyzedCommitService.recordPrCommitsAnalyzed(
                    project, List.of(commitHash), analysis);

            log.info("Recorded PR commit {} as analyzed (branch={}, analysis={})",
                    commitHash.substring(0, Math.min(7, commitHash.length())),
                    sourceBranch,
                    analysis != null ? analysis.getId() : "none");
        } catch (Exception e) {
            log.warn("Failed to record PR commit as analyzed (non-critical): branch={}, error={}",
                    sourceBranch, e.getMessage());
        }
    }

    /**
     * Ensures RAG index is up-to-date for the PR target branch.
     * <p>
     * For PRs targeting the main branch:
     * - Checks if the main RAG index commit matches the current target branch HEAD
     * - If outdated, performs incremental update before analysis
     * <p>
     * For PRs targeting non-main branches with multi-branch enabled:
     * - First ensures the main index is up to date
     * - Then ensures branch index exists and is up to date for the target branch
     * <p>
     * This ensures analysis always uses the most current codebase context.
     */
    private void ensureRagIndexForTargetBranch(Project project, String targetBranch, EventConsumer consumer) {
        if (ragOperationsService == null) {
            log.debug("RagOperationsService not available - skipping RAG index check for target branch");
            return;
        }

        try {
            boolean ready = ragOperationsService.ensureRagIndexUpToDate(
                    project,
                    targetBranch,
                    consumer::accept);
            if (ready) {
                log.info("RAG index ensured up-to-date for PR target branch: project={}, branch={}",
                        project.getId(), targetBranch);
            }
        } catch (Exception e) {
            log.warn(
                    "Failed to ensure RAG index up-to-date for target branch (non-critical): project={}, branch={}, error={}",
                    project.getId(), targetBranch, e.getMessage());
        }
    }

    /**
     * Publishes an AnalysisStartedEvent for PR analysis.
     */
    private void publishAnalysisStartedEvent(Project project, PrProcessRequest request, String correlationId) {
        if (eventPublisher == null) {
            return;
        }
        try {
            AnalysisStartedEvent event = new AnalysisStartedEvent(
                    this,
                    correlationId,
                    project.getId(),
                    project.getName(),
                    AnalysisStartedEvent.AnalysisType.PULL_REQUEST,
                    request.getSourceBranchName(),
                    null // jobId not available at this level
            );
            eventPublisher.publishEvent(event);
            log.debug("Published AnalysisStartedEvent for PR analysis: project={}, pr={}",
                    project.getId(), request.getPullRequestId());
        } catch (Exception e) {
            log.warn("Failed to publish AnalysisStartedEvent: {}", e.getMessage());
        }
    }

    /**
     * Publishes an AnalysisCompletedEvent for PR analysis.
     */
    private void publishAnalysisCompletedEvent(Project project, PrProcessRequest request,
                                               String correlationId, Instant startTime,
                                               AnalysisCompletedEvent.CompletionStatus status, int issuesFound,
                                               int filesAnalyzed, String errorMessage) {
        if (eventPublisher == null) {
            return;
        }
        try {
            Duration duration = Duration.between(startTime, Instant.now());
            Map<String, Object> metrics = new HashMap<>();
            metrics.put("prNumber", request.getPullRequestId());
            metrics.put("targetBranch", request.getTargetBranchName());
            metrics.put("sourceBranch", request.getSourceBranchName());
            metrics.put("commitHash", request.getCommitHash());
            if (request.getPrTitle() != null) {
                metrics.put("prTitle", request.getPrTitle());
            }
            if (request.getPrDescription() != null) {
                metrics.put("prDescription", request.getPrDescription());
            }

            AnalysisCompletedEvent event = new AnalysisCompletedEvent(
                    this,
                    correlationId,
                    project.getId(),
                    null, // jobId not available at this level
                    status,
                    duration,
                    issuesFound,
                    filesAnalyzed,
                    errorMessage,
                    metrics,
                    project.getWorkspace().getName(),
                    project.getNamespace(),
                    request.getPullRequestId());
            eventPublisher.publishEvent(event);
            log.debug("Published AnalysisCompletedEvent for PR analysis: project={}, pr={}, status={}, duration={}ms",
                    project.getId(), request.getPullRequestId(), status, duration.toMillis());
        } catch (Exception e) {
            log.warn("Failed to publish AnalysisCompletedEvent: {}", e.getMessage());
        }
    }
}
