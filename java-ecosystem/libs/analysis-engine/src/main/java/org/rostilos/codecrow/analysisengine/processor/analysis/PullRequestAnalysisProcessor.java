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
import org.rostilos.codecrow.core.service.FileSnapshotService;
import org.rostilos.codecrow.core.service.PrIssueTrackingService;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequestImpl;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.FileContentDto;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.PrEnrichmentDataDto;
import org.rostilos.codecrow.analysisengine.exception.AnalysisLockedException;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.analysisengine.service.PullRequestService;
import org.rostilos.codecrow.analysisapi.rag.RagOperationsService;
import org.rostilos.codecrow.analysisengine.service.gitgraph.GitGraphSyncService;
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
import java.util.stream.Collectors;

import org.rostilos.codecrow.analysisengine.util.DiffFingerprintUtil;
import org.rostilos.codecrow.core.model.gitgraph.CommitNode;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;

/**
 * Generic service that handles pull request analysis.
 * Uses VCS-specific services via VcsServiceFactory for provider-specific operations.
 */
@Service
public class PullRequestAnalysisProcessor {
    private static final Logger log = LoggerFactory.getLogger(PullRequestAnalysisProcessor.class);

    private final CodeAnalysisService codeAnalysisService;
    private final PullRequestService pullRequestService;
    private final AiAnalysisClient aiAnalysisClient;
    private final VcsServiceFactory vcsServiceFactory;
    private final AnalysisLockService analysisLockService;
    private final RagOperationsService ragOperationsService;
    private final ApplicationEventPublisher eventPublisher;
    private final GitGraphSyncService gitGraphSyncService;
    private final VcsClientProvider vcsClientProvider;
    private final FileSnapshotService fileSnapshotService;
    private final PrIssueTrackingService prIssueTrackingService;

    public PullRequestAnalysisProcessor(
            PullRequestService pullRequestService,
            CodeAnalysisService codeAnalysisService,
            AiAnalysisClient aiAnalysisClient,
            VcsServiceFactory vcsServiceFactory,
            AnalysisLockService analysisLockService,
            GitGraphSyncService gitGraphSyncService,
            VcsClientProvider vcsClientProvider,
            FileSnapshotService fileSnapshotService,
            PrIssueTrackingService prIssueTrackingService,
            @Autowired(required = false) RagOperationsService ragOperationsService,
            @Autowired(required = false) ApplicationEventPublisher eventPublisher
    ) {
        this.codeAnalysisService = codeAnalysisService;
        this.pullRequestService = pullRequestService;
        this.aiAnalysisClient = aiAnalysisClient;
        this.vcsServiceFactory = vcsServiceFactory;
        this.analysisLockService = analysisLockService;
        this.ragOperationsService = ragOperationsService;
        this.eventPublisher = eventPublisher;
        this.gitGraphSyncService = gitGraphSyncService;
        this.vcsClientProvider = vcsClientProvider;
        this.fileSnapshotService = fileSnapshotService;
        this.prIssueTrackingService = prIssueTrackingService;
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
            log.info("Using pre-acquired lock: {} for project={}, PR={}", lockKey, project.getId(), request.getPullRequestId());
        } else {
            Optional<String> acquiredLock = analysisLockService.acquireLockWithWait(
                    project,
                    request.getSourceBranchName(),
                    AnalysisLockType.PR_ANALYSIS,
                    request.getCommitHash(),
                    request.getPullRequestId(),
                    consumer::accept
            );

            if (acquiredLock.isEmpty()) {
                String message = String.format(
                        "Failed to acquire lock after %d minutes for project=%s, PR=%d, branch=%s. Another analysis is still in progress.",
                        analysisLockService.getLockWaitTimeoutMinutes(),
                        project.getId(),
                        request.getPullRequestId(),
                        request.getSourceBranchName()
                );
                log.warn(message);
                
                // Publish failed event due to lock timeout
                publishAnalysisCompletedEvent(project, request, correlationId, startTime, 
                        AnalysisCompletedEvent.CompletionStatus.FAILED, 0, 0, "Lock acquisition timeout");
                
                throw new AnalysisLockedException(
                        AnalysisLockType.PR_ANALYSIS.name(),
                        request.getSourceBranchName(),
                        project.getId()
                );
            }
            lockKey = acquiredLock.get();
        }

        try {
            PullRequest pullRequest = pullRequestService.createOrUpdatePullRequest(
                    request.getProjectId(),
                    request.getPullRequestId(),
                    request.getCommitHash(),
                    request.getSourceBranchName(),
                    request.getTargetBranchName(),
                    project
            );

            EVcsProvider provider = ProjectVcsInfoRetriever.getVcsProvider(project);
            VcsReportingService reportingService = vcsServiceFactory.getReportingService(provider);

            if (postAnalysisCacheIfExist(project, pullRequest, request.getCommitHash(), request.getPullRequestId(), reportingService, request.getPlaceholderCommentId())) {
                publishAnalysisCompletedEvent(project, request, correlationId, startTime,
                        AnalysisCompletedEvent.CompletionStatus.SUCCESS, 0, 0, null);
                return Map.of("status", "cached", "cached", true);
            }

            // --- Fallback cache: same commit hash, any PR number (handles close/reopen) ---
            Optional<CodeAnalysis> commitHashHit = codeAnalysisService.getAnalysisByCommitHash(
                    project.getId(), request.getCommitHash());
            if (commitHashHit.isPresent()) {
                log.info("Commit-hash cache hit for project={}, commit={} (source PR={}). Cloning for PR={}.",
                        project.getId(), request.getCommitHash(),
                        commitHashHit.get().getPrNumber(), request.getPullRequestId());
                CodeAnalysis cloned = codeAnalysisService.cloneAnalysisForPr(
                        commitHashHit.get(), project, request.getPullRequestId(),
                        request.getCommitHash(), request.getTargetBranchName(),
                        request.getSourceBranchName(), commitHashHit.get().getDiffFingerprint());

                // Persist PR-level snapshots for the source code viewer
                persistPrSnapshotsForCacheHit(pullRequest, cloned, commitHashHit.get(), project,
                        request.getCommitHash(), null);

                try {
                    reportingService.postAnalysisResults(cloned, project,
                            request.getPullRequestId(), pullRequest.getId(),
                            request.getPlaceholderCommentId());
                } catch (IOException e) {
                    log.error("Failed to post commit-hash cached results to VCS: {}", e.getMessage(), e);
                }
                publishAnalysisCompletedEvent(project, request, correlationId, startTime,
                        AnalysisCompletedEvent.CompletionStatus.SUCCESS, 0, 0, null);
                return Map.of("status", "cached_by_commit", "cached", true);
            }

            // Get all previous analyses for this PR to provide full issue history to AI
            List<CodeAnalysis> allPrAnalyses = codeAnalysisService.getAllPrAnalyses(
                    project.getId(),
                    request.getPullRequestId()
            );
            
            // Get the most recent analysis for incremental diff calculation
            Optional<CodeAnalysis> previousAnalysis = allPrAnalyses.isEmpty() 
                    ? Optional.empty() 
                    : Optional.of(allPrAnalyses.get(0));

            // Ensure branch index exists for TARGET branch (e.g., "1.2.1-rc")
            // This is where the PR will merge TO - we want RAG context from this branch
            ensureRagIndexForTargetBranch(project, request.getTargetBranchName(), consumer);

            VcsAiClientService aiClientService = vcsServiceFactory.getAiClientService(provider);
            AiAnalysisRequest aiRequest = aiClientService.buildAiAnalysisRequest(
                    project, request, previousAnalysis, allPrAnalyses);

            // --- Diff fingerprint cache: same code changes, different PR/commit ---
            String diffFingerprint = DiffFingerprintUtil.compute(aiRequest.getRawDiff());
            if (diffFingerprint != null) {
                Optional<CodeAnalysis> fingerprintHit = codeAnalysisService.getAnalysisByDiffFingerprint(
                        project.getId(), diffFingerprint);
                if (fingerprintHit.isPresent()) {
                    log.info("Diff fingerprint cache hit for project={}, fingerprint={} (source PR={}). Cloning for PR={}.",
                            project.getId(), diffFingerprint.substring(0, 8) + "...",
                            fingerprintHit.get().getPrNumber(), request.getPullRequestId());
                    // TODO: Option B — LIGHTWEIGHT mode: instead of full clone, reuse Stage 1 issues
                    //       but re-run Stage 2 cross-file analysis against the new target branch context.
                    CodeAnalysis cloned = codeAnalysisService.cloneAnalysisForPr(
                            fingerprintHit.get(), project, request.getPullRequestId(),
                            request.getCommitHash(), request.getTargetBranchName(),
                            request.getSourceBranchName(), diffFingerprint);

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
                    publishAnalysisCompletedEvent(project, request, correlationId, startTime,
                            AnalysisCompletedEvent.CompletionStatus.SUCCESS, 0, 0, null);
                    return Map.of("status", "cached_by_fingerprint", "cached", true);
                }
            }

            Map<String, Object> aiResponse = aiAnalysisClient.performAnalysis(aiRequest, event -> {
                try {
                    log.debug("Received event from AI client: type={}", event.get("type"));
                    consumer.accept(event);
                    log.debug("Event forwarded to consumer successfully");
                } catch (Exception ex) {
                    log.error("Event consumer failed: {}", ex.getMessage(), ex);
                }
            });

            // === Extract file contents from enrichment data for line hash computation ===
            Map<String, String> fileContents = extractFileContents(aiRequest);

            // === VCS fallback: when enrichment data is empty (disabled, failed, or provider-specific),
            //     fetch file contents directly from VCS to ensure source viewer always has data ===
            if (fileContents.isEmpty()) {
                log.info("Enrichment file contents empty — falling back to direct VCS file fetch for PR {} (project={})",
                        request.getPullRequestId(), project.getId());
                fileContents = fetchFileContentsFromVcs(project, aiRequest.getChangedFiles(), request.getCommitHash());
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
                    fileContents
            );
            
            int issuesFound = newAnalysis.getIssues() != null ? newAnalysis.getIssues().size() : 0;

            // === Persist file snapshots at PR level for the source code viewer ===
            // Accumulates across iterations: 2nd run adds new files, keeps old ones.
            try {
                fileSnapshotService.persistSnapshotsForPr(pullRequest, newAnalysis, fileContents, request.getCommitHash());
            } catch (Exception snapEx) {
                log.warn("Failed to persist file snapshots (non-critical): {}", snapEx.getMessage());
            }

            // === Deterministic PR issue tracking against previous iteration ===
            try {
                if (previousAnalysis.isPresent()) {
                    Map<String, String> prevFileContents = fileSnapshotService.getFileContentsMap(
                            previousAnalysis.get().getId());
                    prIssueTrackingService.trackPrIteration(
                            newAnalysis, previousAnalysis.get(), fileContents, prevFileContents);
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
                        request.getPlaceholderCommentId()
                );
            } catch (IOException e) {
                log.error("Failed to post analysis results to VCS: {}", e.getMessage(), e);
                consumer.accept(Map.of(
                        "type", "warning",
                        "message", "Analysis completed but failed to post results to VCS: " + e.getMessage()
                ));
            }

            // === DAG: Mark PR commits as ANALYZED ===
            markPrCommitsAnalyzed(project, request.getSourceBranchName(), request.getCommitHash(), newAnalysis);
            
            // Publish successful completion event
            publishAnalysisCompletedEvent(project, request, correlationId, startTime,
                    AnalysisCompletedEvent.CompletionStatus.SUCCESS, issuesFound, 
                    aiRequest.getChangedFiles() != null ? aiRequest.getChangedFiles().size() : 0, null);

            return aiResponse;
        } catch (IOException e) {
            log.error("IOException during PR analysis: {}", e.getMessage(), e);
            consumer.accept(Map.of(
                    "type", "error",
                    "message", "Analysis failed due to I/O error: " + e.getMessage()
            ));
            
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

    /**
     * Extract file contents from the AI analysis request's enrichment data.
     * Returns a map of filePath → raw file content suitable for line hash computation.
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
                        (a, b) -> a   // in case of duplicates, keep first
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
    private Map<String, String> fetchFileContentsFromVcs(Project project, List<String> changedFiles, String commitHash) {
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
                    100_000  // 100 KB max per file, consistent with enrichment service
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
     *   <li>Copy PR-level snapshots from the source analysis's original PR (fast, no VCS calls)</li>
     *   <li>If source PR has no snapshots, fetch from VCS using the provided changed-file list
     *       or, as a last resort, file paths extracted from the cloned analysis's issues</li>
     * </ol>
     *
     * @param pullRequest    the current PR to persist snapshots for
     * @param cloned         the cloned analysis
     * @param sourceAnalysis the original (cache-hit) analysis
     * @param project        the project
     * @param commitHash     the commit hash for VCS fallback
     * @param changedFiles   explicit changed-file list (may be null for commit-hash cache)
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

    protected boolean postAnalysisCacheIfExist(
            Project project, 
            PullRequest pullRequest, 
            String commitHash, 
            Long prId,
            VcsReportingService reportingService,
            String placeholderCommentId
    ) {
        Optional<CodeAnalysis> cachedAnalysis = codeAnalysisService.getCodeAnalysisCache(
                project.getId(),
                commitHash,
                prId
        );

        if (cachedAnalysis.isPresent()) {
            try {
                reportingService.postAnalysisResults(
                        cachedAnalysis.get(),
                        project,
                        prId,
                        pullRequest.getId(),
                        placeholderCommentId
                );
            } catch (IOException e) {
                log.error("Failed to post cached analysis results to VCS: {}", e.getMessage(), e);
            }
            return true;
        }
        return false;
    }
    
    /**
     * After successful PR analysis, sync the source branch graph and mark all unanalyzed
     * commits from HEAD backwards to the first analyzed ancestor as ANALYZED.
     * These commits are the "work" covered by this PR's diff.
     *
     * @param project        the project
     * @param sourceBranch   the PR source branch (where the commits live)
     * @param commitHash     the HEAD commit of the source branch
     * @param analysis       the CodeAnalysis to link, or null for cache-hit scenarios
     */
    private void markPrCommitsAnalyzed(Project project, String sourceBranch, String commitHash, CodeAnalysis analysis) {
        try {
            var vcsConnection = project.getEffectiveVcsConnection();
            if (vcsConnection == null) return;

            Map<String, CommitNode> nodeMap = gitGraphSyncService.syncBranchGraph(
                    project, vcsClientProvider.getClient(vcsConnection), sourceBranch, 100);

            if (nodeMap.isEmpty() || commitHash == null) return;

            List<String> unanalyzed = gitGraphSyncService.findUnanalyzedCommitRange(nodeMap, commitHash);
            if (unanalyzed == null || unanalyzed.isEmpty()) {
                log.debug("PR commits already analyzed in DAG (or HEAD not found) for branch={}", sourceBranch);
                return;
            }

            if (analysis != null && analysis.getId() != null) {
                gitGraphSyncService.markCommitsAnalyzed(project.getId(), unanalyzed, analysis);
            } else {
                gitGraphSyncService.markCommitsAnalyzed(project.getId(), unanalyzed);
            }
            log.info("DAG: Marked {} PR commits as ANALYZED (branch={}, analysis={})",
                    unanalyzed.size(), sourceBranch,
                    analysis != null ? analysis.getId() : "none");
        } catch (Exception e) {
            log.warn("Failed to mark PR commits in DAG (non-critical): branch={}, error={}", sourceBranch, e.getMessage());
        }
    }

    /**
     * Ensures RAG index is up-to-date for the PR target branch.
     * 
     * For PRs targeting the main branch:
     * - Checks if the main RAG index commit matches the current target branch HEAD
     * - If outdated, performs incremental update before analysis
     * 
     * For PRs targeting non-main branches with multi-branch enabled:
     * - First ensures the main index is up to date
     * - Then ensures branch index exists and is up to date for the target branch
     * 
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
                    consumer::accept
            );
            if (ready) {
                log.info("RAG index ensured up-to-date for PR target branch: project={}, branch={}", 
                        project.getId(), targetBranch);
            }
        } catch (Exception e) {
            log.warn("Failed to ensure RAG index up-to-date for target branch (non-critical): project={}, branch={}, error={}",
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
                    metrics
            );
            eventPublisher.publishEvent(event);
            log.debug("Published AnalysisCompletedEvent for PR analysis: project={}, pr={}, status={}, duration={}ms", 
                    project.getId(), request.getPullRequestId(), status, duration.toMillis());
        } catch (Exception e) {
            log.warn("Failed to publish AnalysisCompletedEvent: {}", e.getMessage());
        }
    }
}
