package org.rostilos.codecrow.pipelineagent.generic.service;

import java.io.IOException;
import java.security.GeneralSecurityException;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequestImpl;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiRequestPreviousIssueDTO;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.FileContentDto;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.PrEnrichmentDataDto;
import org.rostilos.codecrow.analysisengine.dto.request.processor.AnalysisProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.analysisengine.service.pr.PrFileEnrichmentService;
import org.rostilos.codecrow.analysisengine.service.pr.PrIncrementalCompatibility;
import org.rostilos.codecrow.analysisengine.service.pr.PullRequestDiffPreparationService;
import org.rostilos.codecrow.analysisengine.service.pr.PullRequestDiffPreparationService.PreparedDiff;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsAiClientService;
import org.rostilos.codecrow.analysisengine.util.DiffParser;
import org.rostilos.codecrow.analysisengine.util.ReviewAnalysisBehavior;
import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisMode;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.rostilos.codecrow.vcsclient.model.VcsPullRequestChangeManifest;
import org.rostilos.codecrow.vcsclient.utils.VcsConnectionCredentialsExtractor;
import org.rostilos.codecrow.vcsclient.utils.VcsConnectionCredentialsExtractor.VcsConnectionCredentials;
import org.rostilos.codecrow.plugins.ProjectCapabilities;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Template for provider-backed AI request construction. Remote VCS reads are
 * performed through the authorized client returned by {@link VcsClientProvider};
 * subclasses only identify their provider.
 */
public abstract class AbstractVcsAiClientService implements VcsAiClientService {
    private static final java.util.regex.Pattern FULL_GIT_OBJECT_ID =
            java.util.regex.Pattern.compile("(?i)^(?:[0-9a-f]{40}|[0-9a-f]{64})$");
    private final Logger log = LoggerFactory.getLogger(getClass());
    private final TokenEncryptionService tokenEncryptionService;
    private final VcsClientProvider vcsClientProvider;
    private final VcsConnectionCredentialsExtractor credentialsExtractor;
    private final PrFileEnrichmentService enrichmentService;
    private final TaskContextEnrichmentService taskContextEnrichmentService;
    private final TaskHistoryContextService taskHistoryContextService;
    private final PullRequestDiffPreparationService diffPreparationService;
    private final ProjectCapabilitySelectionService capabilitySelectionService;

    protected AbstractVcsAiClientService(
            TokenEncryptionService tokenEncryptionService,
            VcsClientProvider vcsClientProvider,
            PrFileEnrichmentService enrichmentService,
            TaskContextEnrichmentService taskContextEnrichmentService,
            TaskHistoryContextService taskHistoryContextService,
            ProjectCapabilitySelectionService capabilitySelectionService,
            PullRequestDiffPreparationService diffPreparationService) {
        this.tokenEncryptionService = tokenEncryptionService;
        this.vcsClientProvider = vcsClientProvider;
        this.credentialsExtractor = new VcsConnectionCredentialsExtractor(tokenEncryptionService);
        this.enrichmentService = enrichmentService;
        this.taskContextEnrichmentService = taskContextEnrichmentService;
        this.taskHistoryContextService = taskHistoryContextService;
        this.capabilitySelectionService = capabilitySelectionService;
        this.diffPreparationService = diffPreparationService;
    }

    @Override
    public final List<AiAnalysisRequest> buildAiAnalysisRequests(
            Project project,
            AnalysisProcessRequest request,
            Optional<CodeAnalysis> previousAnalysis) throws GeneralSecurityException {
        return buildAiAnalysisRequests(project, request, previousAnalysis, List.of());
    }

    @Override
    public final List<AiAnalysisRequest> buildAiAnalysisRequests(
            Project project,
            AnalysisProcessRequest request,
            Optional<CodeAnalysis> previousAnalysis,
            List<CodeAnalysis> allPrAnalyses) throws GeneralSecurityException {
        if (request.getAnalysisType() == AnalysisType.BRANCH_ANALYSIS) {
            return List.of(buildBranchAnalysisRequest(
                    project, (BranchProcessRequest) request, previousAnalysis, null, null, null));
        }
        return buildPullRequestAnalysis(
                project, (PrProcessRequest) request, previousAnalysis, allPrAnalyses);
    }

    private List<AiAnalysisRequest> buildPullRequestAnalysis(
            Project project,
            PrProcessRequest request,
            Optional<CodeAnalysis> previousAnalysis,
            List<CodeAnalysis> allPrAnalyses) throws GeneralSecurityException {
        RepositoryInfo repository = repositoryInfo(project);
        AIConnection aiConnection = project.getAiBinding().getAiConnection();
        String previousCommit = null;
        String currentCommit = request.getCommitHash();
        String currentBehaviorDigest = ReviewAnalysisBehavior.digestFor(
                project, aiConnection, providerKey());
        PullRequestData pullRequest = pullRequestData(
                null, null, request.sourceBranchName, request.targetBranchName,
                null, currentCommit);
        PreparedDiff preparedDiff = PreparedDiff.empty(previousCommit, currentCommit);

        log.info("Building pull request analysis: project={}, AI model={}, provider={}, connection={}",
                project.getId(), aiConnection.getAiModel(), aiConnection.getProviderKey(), aiConnection.getId());

        try {
            VcsClient client = vcsClientProvider.getClient(repository.connection());
            try {
                var metadata = client.getPullRequest(
                        repository.workspace(), repository.repoSlug(), request.getPullRequestId());
                pullRequest = pullRequestData(
                        metadata.title(),
                        metadata.description(),
                        metadata.sourceBranch(),
                        metadata.targetBranch(),
                        metadata.baseCommit(),
                        metadata.headCommit());
            } catch (Exception metadataError) {
                log.warn("PR metadata enrichment failed for project={}, PR={}; "
                                + "continuing with webhook identity: {}",
                        project.getId(), request.getPullRequestId(), metadataError.getMessage());
            }

            if (hasText(pullRequest.sourceBranch())) {
                request.sourceBranchName = pullRequest.sourceBranch();
            }
            if (hasText(pullRequest.targetBranch())) {
                request.targetBranchName = pullRequest.targetBranch();
            }
            if (hasText(pullRequest.headCommit())) {
                currentCommit = pullRequest.headCommit();
                request.commitHash = currentCommit;
            }

            previousCommit = PrIncrementalCompatibility.compatiblePreviousHead(
                    previousAnalysis.orElse(null),
                    request.getPullRequestId(),
                    pullRequest.baseCommit(),
                    currentCommit,
                    currentBehaviorDigest,
                    (ancestor, descendant) -> client.isCommitAncestor(
                            repository.workspace(), repository.repoSlug(),
                            ancestor, descendant))
                    .orElse(null);

            String diff;
            boolean nativeDiffFallback = false;
            if (hasText(pullRequest.baseCommit()) && hasText(pullRequest.headCommit())) {
                try {
                    diff = client.getCommitRangeDiff(
                            repository.workspace(), repository.repoSlug(),
                            pullRequest.baseCommit(), pullRequest.headCommit());
                } catch (IOException rangeError) {
                    log.warn("Commit-range PR diff failed for project={}, PR={}; "
                                    + "using provider-native PR diff: {}",
                            project.getId(), request.getPullRequestId(), rangeError.getMessage());
                    nativeDiffFallback = true;
                    diff = client.getPullRequestDiff(
                            repository.workspace(), repository.repoSlug(),
                            request.getPullRequestId());
                }
            } else {
                log.warn("PR metadata did not include both commit IDs for project={}, PR={}; "
                                + "using provider-native PR diff",
                        project.getId(), request.getPullRequestId());
                nativeDiffFallback = true;
                diff = client.getPullRequestDiff(
                        repository.workspace(), repository.repoSlug(),
                        request.getPullRequestId());
            }

            VcsPullRequestChangeManifest manifest;
            try {
                manifest = client.getPullRequestChangeManifest(
                        repository.workspace(), repository.repoSlug(),
                        request.getPullRequestId());
            } catch (Exception manifestError) {
                log.warn("PR path manifest enrichment failed for project={}, PR={}; "
                                + "continuing with reduced current-head context: {}",
                        project.getId(), request.getPullRequestId(),
                        manifestError.getMessage());
                manifest = VcsPullRequestChangeManifest.unavailable(
                        "provider manifest fetch failed: " + manifestError.getClass().getSimpleName());
            }

            if (nativeDiffFallback || manifest.isComplete() || !manifest.changes().isEmpty()) {
                try {
                    var confirmedMetadata = client.getPullRequest(
                            repository.workspace(), repository.repoSlug(),
                            request.getPullRequestId());
                    if (!sameSnapshot(
                            pullRequest.baseCommit(), pullRequest.headCommit(),
                            confirmedMetadata.baseCommit(), confirmedMetadata.headCommit())) {
                        if (nativeDiffFallback
                                && hasText(confirmedMetadata.baseCommit())
                                && hasText(confirmedMetadata.headCommit())) {
                            log.warn("PR changed while acquiring a provider-native diff for project={}, "
                                            + "PR={}; reacquiring an immutable commit-range snapshot",
                                    project.getId(), request.getPullRequestId());
                            final String confirmedDiff;
                            try {
                                confirmedDiff = client.getCommitRangeDiff(
                                        repository.workspace(), repository.repoSlug(),
                                        confirmedMetadata.baseCommit(), confirmedMetadata.headCommit());
                            } catch (Exception reacquireError) {
                                throw new ImmutablePrSnapshotAcquisitionException(
                                        "Could not reacquire the confirmed pull-request snapshot",
                                        reacquireError);
                            }
                            diff = confirmedDiff;
                            pullRequest = pullRequestData(
                                    confirmedMetadata.title(), confirmedMetadata.description(),
                                    confirmedMetadata.sourceBranch(), confirmedMetadata.targetBranch(),
                                    confirmedMetadata.baseCommit(), confirmedMetadata.headCommit());
                            currentCommit = confirmedMetadata.headCommit();
                            request.commitHash = currentCommit;
                            if (hasText(pullRequest.sourceBranch())) {
                                request.sourceBranchName = pullRequest.sourceBranch();
                            }
                            if (hasText(pullRequest.targetBranch())) {
                                request.targetBranchName = pullRequest.targetBranch();
                            }
                            previousCommit = PrIncrementalCompatibility.compatiblePreviousHead(
                                    previousAnalysis.orElse(null),
                                    request.getPullRequestId(),
                                    pullRequest.baseCommit(),
                                    currentCommit,
                                    currentBehaviorDigest,
                                    (ancestor, descendant) -> client.isCommitAncestor(
                                            repository.workspace(), repository.repoSlug(),
                                            ancestor, descendant))
                                    .orElse(null);
                            nativeDiffFallback = false;
                            // The live manifest was acquired before the confirmed
                            // head. Keep the immutable diff, but never bind that
                            // unproven inventory to the new snapshot.
                            manifest = VcsPullRequestChangeManifest.unavailable(
                                    "provider-native diff reacquired at confirmed head; "
                                            + "manifest snapshot is unproven");
                        } else {
                            log.warn("PR changed while acquiring the path manifest for project={}, PR={}; "
                                            + "discarding the live manifest and continuing without an exact overlay",
                                    project.getId(), request.getPullRequestId());
                            manifest = VcsPullRequestChangeManifest.unavailable(
                                    "pull request changed during manifest acquisition");
                        }
                    }
                } catch (Exception confirmationError) {
                    if (confirmationError instanceof ImmutablePrSnapshotAcquisitionException immutableError) {
                        throw immutableError;
                    }
                    if (nativeDiffFallback) {
                        log.warn("Provider-native PR diff snapshot could not be confirmed for "
                                        + "project={}, PR={}; continuing as a FULL review with "
                                        + "no exact overlay: {}",
                                project.getId(), request.getPullRequestId(),
                                confirmationError.getMessage());
                        previousCommit = null;
                        manifest = VcsPullRequestChangeManifest.unavailable(
                                "provider-native diff snapshot confirmation failed: "
                                        + confirmationError.getClass().getSimpleName());
                    } else {
                        log.warn("Could not confirm PR snapshot after manifest acquisition for project={}, "
                                        + "PR={}; continuing without an exact overlay: {}",
                                project.getId(), request.getPullRequestId(),
                                confirmationError.getMessage());
                        manifest = VcsPullRequestChangeManifest.unavailable(
                                "manifest snapshot confirmation failed: "
                                        + confirmationError.getClass().getSimpleName());
                    }
                }
            }

            preparedDiff = diffPreparationService.prepare(
                    project,
                    request.getPullRequestId(),
                    diff,
                    previousCommit,
                    currentCommit,
                    manifest,
                    (base, head) -> client.getCommitRangeDiff(
                            repository.workspace(), repository.repoSlug(), base, head));
        } catch (IOException e) {
            throw new IllegalStateException(
                    "Unable to fetch pull-request changes from the VCS provider: " + e.getMessage(), e);
        } catch (ImmutablePrSnapshotAcquisitionException e) {
            throw new IllegalStateException(
                    "Unable to bind pull-request changes to a confirmed provider head: "
                            + e.getMessage(),
                    e);
        }

        if (preparedDiff.isEmpty()) {
            log.info("Skipping PR request because the provider returned neither reviewable "
                            + "changes nor a path manifest: project={}, PR={}",
                    project.getId(), request.getPullRequestId());
            return List.of();
        }
        boolean contextMaintenanceOnly = !preparedDiff.hasReviewableDiff();
        if (contextMaintenanceOnly) {
            log.info("No direct-review paths in the current PR delta; emitting current-head "
                            + "context maintenance request: project={}, PR={}",
                    project.getId(), request.getPullRequestId());
        }

        CapabilityEnrichment capabilityEnrichment = prepareCapabilityEnrichment(
                repository, currentCommit, preparedDiff.fullChangedFiles(), "pull request");
        PrEnrichmentDataDto enrichment = capabilityEnrichment.enrichment();
        ProjectCapabilities projectCapabilities = capabilityEnrichment.capabilities();
        Map<String, String> taskContext = resolveTaskContext(
                project, request.sourceBranchName, pullRequest.title(), pullRequest.description());
        String taskHistory = resolveTaskHistory(
                project, request, taskContext, pullRequest.title(), pullRequest.description());

        AiAnalysisRequestImpl.Builder<?> builder = baseBuilder(project, request, repository, aiConnection)
                .withPullRequestId(request.getPullRequestId())
                .withAllPrAnalysesData(allPrAnalyses)
                .withPrTitle(pullRequest.title())
                .withPrDescription(pullRequest.description())
                .withTaskContext(taskContext)
                .withTaskHistoryContext(taskHistory)
                .withChangedFiles(preparedDiff.changedFiles())
                .withDeletedFiles(preparedDiff.deletedFiles())
                .withFullPrChangedFiles(preparedDiff.fullChangedFiles())
                .withFullPrDeletedFiles(preparedDiff.fullDeletedFiles())
                .withPullRequestFileManifest(preparedDiff.fullManifest())
                .withPrContextMaintenanceRequired(contextMaintenanceOnly)
                .withDiffSnippets(List.of())
                .withRawDiff(contextMaintenanceOnly
                        ? preparedDiff.maintenanceDiff()
                        : preparedDiff.fullDiff())
                .withTargetBranchName(request.targetBranchName)
                .withSourceBranchName(request.sourceBranchName)
                .withAnalysisMode(preparedDiff.analysisMode())
                .withDeltaDiff(preparedDiff.analysisMode() == AnalysisMode.INCREMENTAL
                        ? preparedDiff.deltaDiff() : null)
                .withPreviousCommitHash(previousCommit)
                .withCurrentCommitHash(currentCommit)
                .withBaseCommitHash(pullRequest.baseCommit())
                .withEnrichmentData(enrichment)
                .withProjectCapabilities(projectCapabilities);

        addVcsCredentials(builder, repository.connection());
        return List.of(builder.build());
    }

    @Override
    public final List<AiAnalysisRequest> buildAiAnalysisRequestsForBranchReconciliation(
            Project project,
            AnalysisProcessRequest request,
            List<AiRequestPreviousIssueDTO> previousIssues,
            Map<String, String> fileContents) throws GeneralSecurityException {
        return buildAiAnalysisRequestsForBranchReconciliation(
                project, request, previousIssues, fileContents, null);
    }

    @Override
    public final List<AiAnalysisRequest> buildAiAnalysisRequestsForBranchReconciliation(
            Project project,
            AnalysisProcessRequest request,
            List<AiRequestPreviousIssueDTO> previousIssues,
            Map<String, String> fileContents,
            String relevantDiff) throws GeneralSecurityException {
        return List.of(buildBranchAnalysisRequest(
                project, (BranchProcessRequest) request, null,
                previousIssues, fileContents, relevantDiff));
    }

    private AiAnalysisRequest buildBranchAnalysisRequest(
            Project project,
            BranchProcessRequest request,
            Optional<CodeAnalysis> previousAnalysis,
            List<AiRequestPreviousIssueDTO> previousIssues,
            Map<String, String> fileContents,
            String relevantDiff) throws GeneralSecurityException {
        RepositoryInfo repository = repositoryInfo(project);
        AIConnection aiConnection = project.getAiBinding().getAiConnection();
        String resolvedCommit = resolveCommitBestEffort(
                repository, request.getCommitHash(), request.getTargetBranchName());
        request.commitHash = resolvedCommit;
        AiAnalysisRequestImpl.Builder<?> builder = baseBuilder(project, request, repository, aiConnection)
                .withPullRequestId(null)
                .withTargetBranchName(request.getTargetBranchName())
                .withCurrentCommitHash(resolvedCommit);

        if (previousIssues != null && !previousIssues.isEmpty()) {
            builder.withPreviousIssues(previousIssues);
        } else if (previousAnalysis != null) {
            builder.withPreviousAnalysisData(previousAnalysis);
        }
        if (fileContents != null && !fileContents.isEmpty()) {
            builder.withReconciliationFileContents(fileContents);
        }
        if (relevantDiff != null && !relevantDiff.isBlank()) {
            builder.withRawDiff(relevantDiff);
        }

        addVcsCredentials(builder, repository.connection());
        return builder.build();
    }

    @Override
    public final List<AiAnalysisRequest> buildDirectPushAnalysisRequests(
            Project project,
            AnalysisProcessRequest request,
            String rawDiff,
            Map<String, String> fileContents,
            List<String> changedFiles) throws GeneralSecurityException {
        BranchProcessRequest branchRequest = (BranchProcessRequest) request;
        RepositoryInfo repository = repositoryInfo(project);
        AIConnection aiConnection = project.getAiBinding().getAiConnection();
        String resolvedCommit = resolveCommitBestEffort(
                repository, branchRequest.getCommitHash(), branchRequest.getTargetBranchName());
        branchRequest.commitHash = resolvedCommit;
        List<String> safeChangedFiles = changedFiles != null ? changedFiles : List.of();
        CapabilityEnrichment capabilityEnrichment = prepareCapabilityEnrichment(
                repository, resolvedCommit, safeChangedFiles, "direct push");
        PrEnrichmentDataDto enrichment = capabilityEnrichment.enrichment();

        AiAnalysisRequestImpl.Builder<?> builder = baseBuilder(
                project, branchRequest, repository, aiConnection)
                .withPullRequestId(null)
                .withTargetBranchName(branchRequest.getTargetBranchName())
                .withCurrentCommitHash(resolvedCommit)
                .withChangedFiles(safeChangedFiles)
                .withDeletedFiles(DiffParser.extractDeletedFiles(rawDiff != null ? rawDiff : ""))
                .withDiffSnippets(DiffParser.extractDiffSnippets(rawDiff != null ? rawDiff : "", 20))
                .withRawDiff(rawDiff)
                .withAnalysisMode(AnalysisMode.FULL)
                .withEnrichmentData(enrichment)
                .withProjectCapabilities(capabilityEnrichment.capabilities());

        addVcsCredentials(builder, repository.connection());
        return List.of(builder.build());
    }

    private AiAnalysisRequestImpl.Builder<?> baseBuilder(
            Project project,
            AnalysisProcessRequest request,
            RepositoryInfo repository,
            AIConnection aiConnection) throws GeneralSecurityException {
        var effectiveConfig = project.getEffectiveConfig();
        var ragConfig = effectiveConfig.ragConfig();
        return AiAnalysisRequestImpl.builder()
                .withProjectId(project.getId())
                .withAnalysisRunKey(request.getAnalysisRunKey())
                .withProjectAiConnection(aiConnection)
                .withProjectVcsConnectionBindingInfo(repository.workspace(), repository.repoSlug())
                .withProjectAiConnectionTokenDecrypted(
                        tokenEncryptionService.decrypt(aiConnection.getApiKeyEncrypted()))
                .withUseLocalMcp(true)
                .withUseMcpTools(effectiveConfig.useMcpTools())
                .withRagEnabled(ragConfig != null && ragConfig.enabled())
                .withMaxAllowedTokens(effectiveConfig.maxAnalysisTokenLimit())
                .withAnalysisBehaviorDigest(ReviewAnalysisBehavior.digestFor(
                        project, aiConnection, providerKey()))
                .withAnalysisType(request.getAnalysisType())
                .withProjectMetadata(project.getWorkspace().getName(), project.getNamespace())
                .withVcsProvider(providerKey())
                .withProjectRules(effectiveConfig.getProjectRulesConfig().toEnabledRulesJson());
    }

    private PrEnrichmentDataDto enrichFiles(
            RepositoryInfo repository,
            String commitHash,
            List<String> changedFiles,
            String operation) {
        if (enrichmentService == null || changedFiles == null || changedFiles.isEmpty()) {
            return PrEnrichmentDataDto.empty();
        }

        VcsClient vcsClient;
        try {
            vcsClient = vcsClientProvider.getClient(repository.connection());
        } catch (Exception e) {
            log.warn("Skipping {} enrichment because the VCS client is unavailable: {}",
                    operation, e.getMessage());
            return PrEnrichmentDataDto.empty();
        }

        PrEnrichmentDataDto enrichment = PrEnrichmentDataDto.empty();
        if (enrichmentService.isEnrichmentEnabled()) {
            try {
                enrichment = enrichmentService.enrichPrFiles(
                        vcsClient, repository.workspace(), repository.repoSlug(), commitHash, changedFiles);
            } catch (Exception e) {
                log.warn("Structured enrichment failed for {}; trying content-only acquisition: {}",
                        operation, e.getMessage());
            }
        }
        if (enrichment.hasData()) return enrichment;

        try {
            return enrichmentService.fetchFileContentsOnly(
                    vcsClient, repository.workspace(), repository.repoSlug(), commitHash, changedFiles);
        } catch (Exception e) {
            log.warn("Skipping {} file-content enrichment: {}", operation, e.getMessage());
            return PrEnrichmentDataDto.empty();
        }
    }

    private CapabilityEnrichment prepareCapabilityEnrichment(
            RepositoryInfo repository,
            String commit,
            List<String> changedFiles,
            String operation) {
        if (capabilitySelectionService == null) {
            return new CapabilityEnrichment(
                    withPathReceipts(
                            enrichFiles(repository, commit, changedFiles, operation),
                            changedFiles,
                            Map.of()),
                    null);
        }
        try {
            VcsClient vcsClient = vcsClientProvider.getClient(repository.connection());
            var plan = capabilitySelectionService.plan(
                    vcsClient, repository.workspace(), repository.repoSlug(), commit,
                    changedFiles);
            PrEnrichmentDataDto enrichment = enrichFiles(
                    repository, commit, plan.enrichmentPaths(), operation);
            ProjectCapabilities capabilities = capabilitySelectionService.complete(
                    plan, enrichment);
            return new CapabilityEnrichment(
                    withPathReceipts(
                            enrichment, changedFiles, plan.enrichmentDispositions()),
                    capabilities);
        } catch (Exception exception) {
            log.warn("Skipping project capability enrichment at commit {}: {}",
                    commit, exception.getMessage());
            return new CapabilityEnrichment(
                    withPathReceipts(
                            enrichFiles(repository, commit, changedFiles, operation),
                            changedFiles,
                            Map.of()),
                    null);
        }
    }

    static PrEnrichmentDataDto withPathReceipts(
            PrEnrichmentDataDto enrichment,
            List<String> requestedPaths,
            Map<String, String> deterministicDispositions) {
        if (requestedPaths == null || requestedPaths.isEmpty()) {
            return enrichment != null ? enrichment : PrEnrichmentDataDto.empty();
        }

        PrEnrichmentDataDto safe = enrichment != null
                ? enrichment
                : PrEnrichmentDataDto.empty();
        Map<String, FileContentDto> byPath = new LinkedHashMap<>();
        if (safe.fileContents() != null) {
            for (FileContentDto file : safe.fileContents()) {
                if (file != null && hasText(file.path())) {
                    byPath.put(normalizePath(file.path()), file);
                }
            }
        }

        List<FileContentDto> completeReceipts = new java.util.ArrayList<>();
        for (String rawPath : new LinkedHashSet<>(requestedPaths)) {
            if (!hasText(rawPath)) continue;
            String path = normalizePath(rawPath);
            FileContentDto existing = byPath.remove(path);
            if (existing != null) {
                completeReceipts.add(existing);
                continue;
            }
            String disposition = deterministicDispositions != null
                    ? deterministicDispositions.get(path)
                    : null;
            completeReceipts.add(FileContentDto.skipped(
                    path, hasText(disposition) ? disposition : "fetch_failed"));
        }
        completeReceipts.addAll(byPath.values());

        long totalSize = completeReceipts.stream()
                .filter(file -> !file.skipped())
                .mapToLong(FileContentDto::sizeBytes)
                .sum();
        int enriched = (int) completeReceipts.stream()
                .filter(file -> !file.skipped())
                .count();
        Map<String, Integer> skipReasons = new LinkedHashMap<>();
        completeReceipts.stream()
                .filter(FileContentDto::skipped)
                .forEach(file -> skipReasons.merge(
                        hasText(file.skipReason()) ? file.skipReason() : "fetch_failed",
                        1,
                        Integer::sum));
        List<?> relationships = safe.relationships() != null
                ? safe.relationships()
                : List.of();
        long processingTime = safe.stats() != null ? safe.stats().processingTimeMs() : 0;
        PrEnrichmentDataDto.EnrichmentStats stats = new PrEnrichmentDataDto.EnrichmentStats(
                requestedPaths.size(),
                enriched,
                completeReceipts.size() - enriched,
                relationships.size(),
                totalSize,
                processingTime,
                Map.copyOf(skipReasons));
        return new PrEnrichmentDataDto(
                List.copyOf(completeReceipts),
                safe.fileMetadata() != null ? safe.fileMetadata() : List.of(),
                safe.relationships() != null ? safe.relationships() : List.of(),
                stats);
    }

    private static String normalizePath(String path) {
        String normalized = path.replace('\\', '/');
        while (normalized.startsWith("./")) normalized = normalized.substring(2);
        while (normalized.startsWith("/")) normalized = normalized.substring(1);
        return normalized;
    }

    private static final class ImmutablePrSnapshotAcquisitionException
            extends Exception {
        private ImmutablePrSnapshotAcquisitionException(
                String message,
                Throwable cause) {
            super(message, cause);
        }
    }

    private String resolveCommitBestEffort(
            RepositoryInfo repository,
            String suppliedCommit,
            String branchName) {
        if (isFullGitObjectId(suppliedCommit)) {
            return suppliedCommit;
        }
        try {
            VcsClient client = vcsClientProvider.getClient(repository.connection());
            String candidate = suppliedCommit;
            if (!hasText(candidate) && hasText(branchName)) {
                candidate = client.getLatestCommitHash(
                        repository.workspace(), repository.repoSlug(), branchName);
            } else if (hasText(candidate)) {
                List<org.rostilos.codecrow.vcsclient.model.VcsCommit> commits =
                        client.getCommitHistory(
                                repository.workspace(), repository.repoSlug(), candidate, 1);
                if (commits != null && !commits.isEmpty()) {
                    candidate = commits.get(0).hash();
                }
            }
            if (hasText(candidate)) {
                return candidate;
            }
        } catch (Exception exception) {
            log.warn("Could not expand commit '{}' for branch '{}'; continuing with "
                            + "the provider-supplied revision: {}",
                    suppliedCommit, branchName, exception.getMessage());
        }
        return suppliedCommit;
    }

    private Map<String, String> resolveTaskContext(
            Project project,
            String sourceBranch,
            String title,
            String description) {
        if (taskContextEnrichmentService == null) {
            return Collections.emptyMap();
        }
        Map<String, String> resolved =
                taskContextEnrichmentService.resolveTaskContext(
                        project, sourceBranch, title, description);
        if (resolved.containsKey("task_key")) {
            return resolved;
        }

        // The task provider may be temporarily unavailable while the task key
        // remains deterministically identifiable from PR metadata. Preserve
        // that key for database association and prior-task evidence lookup.
        Optional<String> fallbackKey = taskContextEnrichmentService.resolveTaskKey(
                project, sourceBranch, title, description);
        if (fallbackKey.isEmpty()) {
            return resolved;
        }
        Map<String, String> withFallbackKey = new LinkedHashMap<>(resolved);
        withFallbackKey.put("task_key", fallbackKey.get());
        return Map.copyOf(withFallbackKey);
    }

    private String resolveTaskHistory(
            Project project,
            PrProcessRequest request,
            Map<String, String> taskContext,
            String title,
            String description) {
        if (taskHistoryContextService == null || taskContextEnrichmentService == null) return "";
        String taskKey = taskContextEnrichmentService.resolveTaskKey(
                project, request.sourceBranchName, title, description).orElse(null);
        return taskHistoryContextService.buildTaskHistoryContext(
                project.getId(), request.getPullRequestId(), taskContext, taskKey);
    }

    private RepositoryInfo repositoryInfo(Project project) {
        var vcsInfo = project.getEffectiveVcsRepoInfo();
        if (vcsInfo == null || vcsInfo.getVcsConnection() == null) {
            throw new IllegalStateException("No VCS connection configured for project: " + project.getId());
        }
        return new RepositoryInfo(
                vcsInfo.getVcsConnection(), vcsInfo.getRepoWorkspace(), vcsInfo.getRepoSlug());
    }

    private void addVcsCredentials(
            AiAnalysisRequestImpl.Builder<?> builder,
            VcsConnection connection) throws GeneralSecurityException {
        VcsConnectionCredentials credentials = credentialsExtractor.extractCredentials(connection);
        builder.withVcsBaseUrl(credentials.vcsBaseUrl());
        if (VcsConnectionCredentialsExtractor.hasAccessToken(credentials)) {
            builder.withAccessToken(credentials.accessToken());
        } else if (VcsConnectionCredentialsExtractor.hasOAuthCredentials(credentials)) {
            builder.withProjectVcsConnectionCredentials(
                    credentials.oAuthClient(), credentials.oAuthSecret());
        } else {
            log.warn("No credentials available for VCS connection type: {}", connection.getConnectionType());
        }
    }

    private String providerKey() {
        return getProvider() == EVcsProvider.BITBUCKET_CLOUD
                ? "bitbucket_cloud"
                : getProvider().getId();
    }

    protected final PullRequestData pullRequestData(
            String title,
            String description,
            String sourceBranch,
            String targetBranch,
            String baseCommit,
            String headCommit) {
        return new PullRequestData(
                title,
                description,
                sourceBranch,
                targetBranch,
                baseCommit,
                headCommit);
    }

    protected static boolean isFullGitObjectId(String value) {
        return value != null && FULL_GIT_OBJECT_ID.matcher(value).matches();
    }

    static boolean sameSnapshot(
            String expectedBase,
            String expectedHead,
            String actualBase,
            String actualHead) {
        return hasText(expectedBase)
                && hasText(expectedHead)
                && expectedBase.equals(actualBase)
                && expectedHead.equals(actualHead);
    }

    private static boolean hasText(String value) {
        return value != null && !value.isBlank();
    }

    protected record RepositoryInfo(
            VcsConnection connection,
            String workspace,
            String repoSlug) {}

    protected record PullRequestData(
            String title,
            String description,
            String sourceBranch,
            String targetBranch,
            String baseCommit,
            String headCommit) {}

    private record CapabilityEnrichment(
            PrEnrichmentDataDto enrichment,
            ProjectCapabilities capabilities) {}
}
