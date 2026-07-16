package org.rostilos.codecrow.pipelineagent.generic.service;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.TreeMap;
import java.util.regex.Pattern;

import okhttp3.OkHttpClient;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequestImpl;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiRequestPreviousIssueDTO;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.FileContentDto;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.PrEnrichmentDataDto;
import org.rostilos.codecrow.analysisengine.dto.request.processor.AnalysisProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.analysisengine.service.pr.PrFileEnrichmentService;
import org.rostilos.codecrow.analysisengine.service.pr.PullRequestDiffPreparationService;
import org.rostilos.codecrow.analysisengine.service.pr.PullRequestDiffPreparationService.PreparedDiff;
import org.rostilos.codecrow.analysisengine.service.vcs.ExactHeadAdmission;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsAiClientService;
import org.rostilos.codecrow.analysisengine.util.AnalysisScopeFilter;
import org.rostilos.codecrow.analysisengine.util.DiffParser;
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
import org.rostilos.codecrow.vcsclient.diff.ExactDiffInventory;
import org.rostilos.codecrow.vcsclient.diff.ExactDiffInventoryParser;
import org.rostilos.codecrow.vcsclient.utils.VcsConnectionCredentialsExtractor;
import org.rostilos.codecrow.vcsclient.utils.VcsConnectionCredentialsExtractor.VcsConnectionCredentials;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Template for provider-backed AI request construction. Subclasses implement
 * only remote VCS reads; all analysis policy and request assembly lives here.
 */
public abstract class AbstractVcsAiClientService implements VcsAiClientService {
    private static final Pattern EXACT_REVISION = Pattern.compile("(?:[0-9a-f]{40}|[0-9a-f]{64})");

    private final Logger log = LoggerFactory.getLogger(getClass());
    private final TokenEncryptionService tokenEncryptionService;
    private final VcsClientProvider vcsClientProvider;
    private final VcsConnectionCredentialsExtractor credentialsExtractor;
    private final PrFileEnrichmentService enrichmentService;
    private final TaskContextEnrichmentService taskContextEnrichmentService;
    private final TaskHistoryContextService taskHistoryContextService;
    private final PullRequestDiffPreparationService diffPreparationService;

    protected AbstractVcsAiClientService(
            TokenEncryptionService tokenEncryptionService,
            VcsClientProvider vcsClientProvider,
            PrFileEnrichmentService enrichmentService,
            TaskContextEnrichmentService taskContextEnrichmentService,
            TaskHistoryContextService taskHistoryContextService,
            PullRequestDiffPreparationService diffPreparationService) {
        this.tokenEncryptionService = tokenEncryptionService;
        this.vcsClientProvider = vcsClientProvider;
        this.credentialsExtractor = new VcsConnectionCredentialsExtractor(tokenEncryptionService);
        this.enrichmentService = enrichmentService;
        this.taskContextEnrichmentService = taskContextEnrichmentService;
        this.taskHistoryContextService = taskHistoryContextService;
        this.diffPreparationService = diffPreparationService;
    }

    protected abstract PullRequestData fetchPullRequest(
            OkHttpClient client,
            RepositoryInfo repository,
            long pullRequestId) throws IOException;

    /**
     * Discovers immutable pull-request coordinates without reading a mutable PR
     * diff. Providers must override this hook before they can use the exact-SHA
     * pull-request path.
     */
    protected PullRequestMetadata fetchPullRequestMetadata(
            OkHttpClient client,
            RepositoryInfo repository,
            long pullRequestId) throws IOException {
        throw new IOException("Exact pull-request metadata is unavailable for provider " + getProvider());
    }

    protected abstract String fetchCommitRangeDiff(
            OkHttpClient client,
            RepositoryInfo repository,
            String baseCommit,
            String headCommit) throws IOException;

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

    @Override
    public final List<AiAnalysisRequest> buildExactAiAnalysisRequests(
            Project project,
            AnalysisProcessRequest request,
            Optional<CodeAnalysis> previousAnalysis,
            List<CodeAnalysis> allPrAnalyses) throws GeneralSecurityException {
        return buildExactAiAnalysisRequests(
                project,
                request,
                previousAnalysis,
                allPrAnalyses,
                ignored -> { });
    }

    @Override
    public final List<AiAnalysisRequest> buildExactAiAnalysisRequests(
            Project project,
            AnalysisProcessRequest request,
            Optional<CodeAnalysis> previousAnalysis,
            List<CodeAnalysis> allPrAnalyses,
            ExactHeadAdmission headAdmission) throws GeneralSecurityException {
        java.util.Objects.requireNonNull(headAdmission, "headAdmission");
        if (request.getAnalysisType() != AnalysisType.PR_REVIEW) {
            throw new IllegalArgumentException(
                    "Exact-SHA acquisition currently requires a pull-request review");
        }
        return buildExactPullRequestAnalysis(
                project, (PrProcessRequest) request, headAdmission);
    }

    private List<AiAnalysisRequest> buildPullRequestAnalysis(
            Project project,
            PrProcessRequest request,
            Optional<CodeAnalysis> previousAnalysis,
            List<CodeAnalysis> allPrAnalyses) throws GeneralSecurityException {
        RepositoryInfo repository = repositoryInfo(project);
        AIConnection aiConnection = project.getAiBinding().getAiConnection();
        String previousCommit = previousAnalysis.map(CodeAnalysis::getCommitHash).orElse(null);
        String currentCommit = request.getCommitHash();
        PullRequestData pullRequest = PullRequestData.empty();
        PreparedDiff preparedDiff = PreparedDiff.empty(previousCommit, currentCommit);

        log.info("Building pull request analysis: project={}, AI model={}, provider={}, connection={}",
                project.getId(), aiConnection.getAiModel(), aiConnection.getProviderKey(), aiConnection.getId());

        try {
            OkHttpClient client = vcsClientProvider.getHttpClient(repository.connection());
            pullRequest = fetchPullRequest(client, repository, request.getPullRequestId());
            preparedDiff = diffPreparationService.prepare(
                    project,
                    request.getPullRequestId(),
                    pullRequest.rawDiff(),
                    previousCommit,
                    currentCommit,
                    (base, head) -> fetchCommitRangeDiff(client, repository, base, head));
        } catch (IOException e) {
            log.warn("Unable to fetch pull request data: {}", e.getMessage());
        }

        if (preparedDiff.isEmpty()) {
            log.info("Skipping analysis because no changed files match the project scope: project={}, PR={}",
                    project.getId(), request.getPullRequestId());
            return List.of();
        }

        PrEnrichmentDataDto enrichment = enrichFiles(
                repository, currentCommit, preparedDiff.changedFiles(), "pull request");
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
                .withDiffSnippets(List.of())
                .withRawDiff(preparedDiff.fullDiff())
                .withTargetBranchName(request.targetBranchName)
                .withSourceBranchName(request.sourceBranchName)
                .withAnalysisMode(preparedDiff.analysisMode())
                .withDeltaDiff(preparedDiff.analysisMode() == AnalysisMode.INCREMENTAL
                        ? preparedDiff.deltaDiff() : null)
                .withPreviousCommitHash(preparedDiff.analysisMode() == AnalysisMode.INCREMENTAL
                        ? previousCommit
                        : pullRequest.baseRevision())
                .withCurrentCommitHash(currentCommit)
                .withEnrichmentData(enrichment);

        addVcsCredentials(builder, repository.connection());
        return List.of(builder.build());
    }

    private List<AiAnalysisRequest> buildExactPullRequestAnalysis(
            Project project,
            PrProcessRequest request,
            ExactHeadAdmission headAdmission) throws GeneralSecurityException {
        RepositoryInfo repository = repositoryInfo(project);
        AIConnection aiConnection = project.getAiBinding().getAiConnection();
        String acceptedHead = request.getCommitHash();
        requireExactRevision(acceptedHead, "accepted webhook head");

        log.info("Building pull request analysis: project={}, AI model={}, provider={}, connection={}",
                project.getId(), aiConnection.getAiModel(), aiConnection.getProviderKey(), aiConnection.getId());

        OkHttpClient client = vcsClientProvider.getHttpClient(repository.connection());
        PullRequestMetadata metadata = fetchExactPullRequestMetadata(
                client, repository, request.getPullRequestId());
        requireExactRevision(metadata.baseSha(), "base");
        requireExactRevision(metadata.headSha(), "head");
        requireExactRevision(metadata.mergeBaseSha(), "merge base");
        if (!acceptedHead.equals(metadata.headSha())) {
            throw new IllegalStateException(
                    "Accepted webhook head does not match the current pull-request head");
        }
        headAdmission.admit(metadata.headSha());

        String fullDiff = fetchRequiredFullDiff(
                client, repository, metadata.baseSha(), metadata.headSha());
        ExactDiffInventory exactInventory = requireCompleteExactInventory(fullDiff);

        // Retain the existing content/limit policy until it accepts the typed
        // inventory directly. Its regex-derived file list is deliberately not
        // trusted at this boundary.
        diffPreparationService.prepare(
                project,
                request.getPullRequestId(),
                fullDiff,
                null,
                metadata.headSha(),
                (base, head) -> fetchExactCommitRangeDiff(client, repository, base, head));
        ExactInventoryPaths exactPaths = exactInventoryPaths(project, exactInventory);

        if (exactPaths.isEmpty()) {
            log.info("Skipping analysis because no changed files match the project scope: project={}, PR={}",
                    project.getId(), request.getPullRequestId());
        }

        PrEnrichmentDataDto enrichment = enrichPullRequestFiles(
                repository, metadata.headSha(), exactPaths.changedFiles());
        String sourceBranch = request.sourceBranchName == null
                        || request.sourceBranchName.isBlank()
                ? metadata.headSha()
                : request.sourceBranchName;
        String targetBranch = request.targetBranchName == null
                        || request.targetBranchName.isBlank()
                ? metadata.baseSha()
                : request.targetBranchName;
        Map<String, String> taskContext = resolveTaskContext(
                project,
                sourceBranch,
                metadata.title(),
                metadata.description());
        String taskHistory = resolveTaskHistory(
                project,
                request,
                taskContext,
                metadata.title(),
                metadata.description());
        String configuredProjectRules = project.getEffectiveConfig()
                .getProjectRulesConfig()
                .toEnabledRulesJson();
        String projectRules = configuredProjectRules == null
                ? "[]"
                : configuredProjectRules;
        PrEnrichmentDataDto.ReviewContext reviewContext =
                new PrEnrichmentDataDto.ReviewContext(
                        PrEnrichmentDataDto.CURRENT_REVIEW_CONTEXT_SCHEMA_VERSION,
                        metadata.title(),
                        metadata.description(),
                        request.getPrAuthorUsername(),
                        taskContext,
                        taskHistory,
                        projectRules,
                        sourceBranch,
                        targetBranch);
        enrichment = enrichment.withReviewContext(reviewContext);

        AiAnalysisRequestImpl.Builder<?> builder = manifestBoundBaseBuilder(
                project, request, repository, aiConnection)
                .withPullRequestId(request.getPullRequestId())
                .withPrTitle(metadata.title())
                .withPrDescription(metadata.description())
                .withTaskContext(taskContext)
                .withTaskHistoryContext(taskHistory)
                .withChangedFiles(exactPaths.changedFiles())
                .withDeletedFiles(exactPaths.deletedFiles())
                .withDiffSnippets(List.of())
                // The immutable manifest owns the exact acquired bytes; the
                // typed inventory schedules exact-head file acquisition and
                // must never erase or rewrite that authoritative artifact.
                .withRawDiff(fullDiff)
                .withAnalysisMode(AnalysisMode.FULL)
                .withDeltaDiff(null)
                .withPreviousCommitHash(metadata.baseSha())
                .withCurrentCommitHash(metadata.headSha())
                .withImmutableSnapshot(
                        metadata.baseSha(), metadata.headSha(), metadata.mergeBaseSha())
                .withEnrichmentData(enrichment)
                .withSourceBranchName(sourceBranch)
                .withTargetBranchName(targetBranch)
                .withProjectRules(projectRules);

        addVcsCredentials(builder, repository.connection());
        return List.of(builder.build());
    }

    private static ExactDiffInventory requireCompleteExactInventory(String rawDiff) {
        ExactDiffInventory inventory = new ExactDiffInventoryParser().parse(rawDiff);
        if (inventory.completeness() != ExactDiffInventory.Completeness.COMPLETE) {
            String gapTypes = inventory.gaps().stream()
                    .map(gap -> gap.type().name())
                    .distinct()
                    .sorted()
                    .reduce((left, right) -> left + "," + right)
                    .orElse(ExactDiffInventory.GapType.MALFORMED.name());
            throw new IllegalStateException(
                    "Exact pull-request diff inventory is incomplete: " + gapTypes);
        }
        return inventory;
    }

    private static ExactInventoryPaths exactInventoryPaths(
            Project project,
            ExactDiffInventory inventory) {
        var scope = AnalysisScopeFilter.scope(project);
        Set<String> changedFiles = new LinkedHashSet<>();
        Set<String> deletedFiles = new LinkedHashSet<>();
        for (ExactDiffInventory.Entry entry : inventory.entries()) {
            if (!scope.includesChange(entry.oldPath(), entry.newPath())) {
                continue;
            }
            if (entry.status() == ExactDiffInventory.ChangeStatus.DELETE) {
                deletedFiles.add(entry.oldPath());
            } else {
                changedFiles.add(entry.newPath());
            }
        }
        return new ExactInventoryPaths(
                List.copyOf(changedFiles), List.copyOf(deletedFiles));
    }

    private record ExactInventoryPaths(
            List<String> changedFiles,
            List<String> deletedFiles) {
        private boolean isEmpty() {
            return changedFiles.isEmpty() && deletedFiles.isEmpty();
        }
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
        AiAnalysisRequestImpl.Builder<?> builder = baseBuilder(project, request, repository, aiConnection)
                .withPullRequestId(null)
                .withTargetBranchName(request.getTargetBranchName())
                .withCurrentCommitHash(request.getCommitHash());

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
        List<String> safeChangedFiles = changedFiles != null ? changedFiles : List.of();
        PrEnrichmentDataDto enrichment = enrichFiles(
                repository, branchRequest.getCommitHash(), safeChangedFiles, "direct push");

        AiAnalysisRequestImpl.Builder<?> builder = baseBuilder(
                project, branchRequest, repository, aiConnection)
                .withPullRequestId(null)
                .withTargetBranchName(branchRequest.getTargetBranchName())
                .withCurrentCommitHash(branchRequest.getCommitHash())
                .withChangedFiles(safeChangedFiles)
                .withDeletedFiles(DiffParser.extractDeletedFiles(rawDiff != null ? rawDiff : ""))
                .withDiffSnippets(DiffParser.extractDiffSnippets(rawDiff != null ? rawDiff : "", 20))
                .withRawDiff(rawDiff)
                .withAnalysisMode(AnalysisMode.FULL)
                .withEnrichmentData(enrichment);

        addVcsCredentials(builder, repository.connection());
        return List.of(builder.build());
    }

    private AiAnalysisRequestImpl.Builder<?> baseBuilder(
            Project project,
            AnalysisProcessRequest request,
            RepositoryInfo repository,
            AIConnection aiConnection) throws GeneralSecurityException {
        return manifestBoundBaseBuilder(project, request, repository, aiConnection)
                .withUseMcpTools(project.getEffectiveConfig().useMcpTools())
                .withProjectRules(
                        project.getEffectiveConfig().getProjectRulesConfig().toEnabledRulesJson());
    }

    /**
     * Builds the common request fields for the manifest-bound candidate path.
     * Exact pull-request acquisition adds its immutable snapshot and review
     * context before the request enters the v2 queue.
     */
    private AiAnalysisRequestImpl.Builder<?> manifestBoundBaseBuilder(
            Project project,
            AnalysisProcessRequest request,
            RepositoryInfo repository,
            AIConnection aiConnection) throws GeneralSecurityException {
        return AiAnalysisRequestImpl.builder()
                .withProjectId(project.getId())
                .withProjectAiConnection(aiConnection)
                .withProjectVcsConnectionBindingInfo(repository.workspace(), repository.repoSlug())
                .withProjectAiConnectionTokenDecrypted(
                        tokenEncryptionService.decrypt(aiConnection.getApiKeyEncrypted()))
                .withUseLocalMcp(true)
                .withMaxAllowedTokens(project.getEffectiveConfig().maxAnalysisTokenLimit())
                .withAnalysisType(request.getAnalysisType())
                .withProjectMetadata(project.getWorkspace().getName(), project.getNamespace())
                .withVcsProvider(providerKey());
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
            log.warn("Unable to obtain a VCS client for {} enrichment (non-critical): {}",
                    operation, e.getMessage());
            return PrEnrichmentDataDto.empty();
        }

        PrEnrichmentDataDto enrichment = PrEnrichmentDataDto.empty();
        if (enrichmentService.isEnrichmentEnabled()) {
            try {
                enrichment = enrichmentService.enrichPrFiles(
                        vcsClient, repository.workspace(), repository.repoSlug(), commitHash, changedFiles);
            } catch (Exception e) {
                log.warn("Unable to enrich {} files (non-critical): {}", operation, e.getMessage());
            }
        }
        if (enrichment.hasData()) return enrichment;

        try {
            return enrichmentService.fetchFileContentsOnly(
                    vcsClient, repository.workspace(), repository.repoSlug(), commitHash, changedFiles);
        } catch (Exception e) {
            log.warn("Unable to fetch {} file contents (non-critical): {}", operation, e.getMessage());
            return PrEnrichmentDataDto.empty();
        }
    }

    private PrEnrichmentDataDto enrichPullRequestFiles(
            RepositoryInfo repository,
            String exactHead,
            List<String> changedFiles) {
        if (changedFiles == null || changedFiles.isEmpty()) {
            return PrEnrichmentDataDto.empty();
        }
        if (enrichmentService == null) {
            throw new IllegalStateException(
                    "Exact-head pull-request file acquisition is unavailable");
        }

        VcsClient vcsClient = vcsClientProvider.getClient(repository.connection());
        PrEnrichmentDataDto fileContents = enrichmentService.fetchFileContentsOnly(
                vcsClient, repository.workspace(), repository.repoSlug(), exactHead, changedFiles);
        if (fileContents == null) {
            throw new IllegalStateException("Exact-head pull-request file acquisition returned no result");
        }
        requireCompleteExactFileAccounting(fileContents, changedFiles);
        return canonicalExactFileContents(fileContents);
    }

    /**
     * Removes operational timing and parser-derived data from the immutable
     * acquisition artifact. Exact source bytes and explicit fetch gaps remain;
     * P1-02 owns the eventual normalized diff/source inventory.
     */
    private static PrEnrichmentDataDto canonicalExactFileContents(
            PrEnrichmentDataDto acquired) {
        List<FileContentDto> files = acquired.fileContents().stream()
                .sorted(Comparator.comparing(FileContentDto::path))
                .toList();
        PrEnrichmentDataDto.EnrichmentStats observed = acquired.stats();
        PrEnrichmentDataDto.EnrichmentStats canonicalStats =
                new PrEnrichmentDataDto.EnrichmentStats(
                        observed.totalFilesRequested(),
                        observed.filesEnriched(),
                        observed.filesSkipped(),
                        0,
                        observed.totalContentSizeBytes(),
                        0,
                        observed.skipReasons() == null
                                ? Map.of()
                                : new TreeMap<>(observed.skipReasons()));
        return new PrEnrichmentDataDto(
                files,
                List.of(),
                List.of(),
                canonicalStats);
    }

    private static void requireCompleteExactFileAccounting(
            PrEnrichmentDataDto enrichment,
            List<String> changedFiles) {
        if (enrichment == null || changedFiles == null) {
            throw new IllegalStateException(
                    "Exact-head pull-request file acquisition returned no complete accounting");
        }
        Set<String> expectedPaths = new HashSet<>();
        for (String path : changedFiles) {
            if (path == null || path.isBlank() || path.indexOf('\0') >= 0
                    || !expectedPaths.add(path)) {
                throw new IllegalStateException(
                        "Exact-head pull-request changed-file inventory is invalid");
            }
        }
        List<FileContentDto> observedFiles = enrichment.fileContents();
        if (observedFiles == null || observedFiles.size() != expectedPaths.size()) {
            throw new IllegalStateException(
                    "Exact-head pull-request file acquisition returned no complete accounting");
        }

        Set<String> observedPaths = new HashSet<>();
        int enrichedCount = 0;
        int skippedCount = 0;
        long contentBytes = 0L;
        for (FileContentDto file : observedFiles) {
            if (file == null || file.path() == null || !expectedPaths.contains(file.path())
                    || !observedPaths.add(file.path())) {
                throw new IllegalStateException(
                        "Exact-head pull-request file acquisition returned conflicting paths");
            }
            if (file.skipped()) {
                if (file.content() != null || file.skipReason() == null
                        || file.skipReason().isBlank()) {
                    throw new IllegalStateException(
                            "Exact-head skipped file is missing its explicit gap reason");
                }
                skippedCount++;
            } else {
                if (file.content() == null
                        || file.sizeBytes() != file.content()
                                .getBytes(StandardCharsets.UTF_8).length) {
                    throw new IllegalStateException(
                            "Exact-head file content is missing or has an invalid byte length");
                }
                enrichedCount++;
                contentBytes = Math.addExact(contentBytes, file.sizeBytes());
            }
        }

        PrEnrichmentDataDto.EnrichmentStats stats = enrichment.stats();
        if (stats == null
                || stats.totalFilesRequested() != expectedPaths.size()
                || stats.filesEnriched() != enrichedCount
                || stats.filesSkipped() != skippedCount
                || stats.totalContentSizeBytes() != contentBytes) {
            throw new IllegalStateException(
                    "Exact-head pull-request file acquisition returned no complete accounting");
        }
    }

    private PullRequestMetadata fetchExactPullRequestMetadata(
            OkHttpClient client,
            RepositoryInfo repository,
            long pullRequestId) {
        try {
            PullRequestMetadata metadata = fetchPullRequestMetadata(client, repository, pullRequestId);
            if (metadata == null) {
                throw new IllegalStateException("Pull-request metadata is missing");
            }
            return metadata;
        } catch (IOException e) {
            throw new IllegalStateException("Unable to fetch exact pull-request metadata", e);
        }
    }

    private String fetchExactCommitRangeDiff(
            OkHttpClient client,
            RepositoryInfo repository,
            String baseRevision,
            String headRevision) throws IOException {
        requireExactRevision(baseRevision, "range base");
        requireExactRevision(headRevision, "range head");
        String diff = fetchCommitRangeDiff(client, repository, baseRevision, headRevision);
        if (diff == null) {
            throw new IOException("Exact commit-range diff is missing");
        }
        return diff;
    }

    private String fetchRequiredFullDiff(
            OkHttpClient client,
            RepositoryInfo repository,
            String baseRevision,
            String headRevision) {
        try {
            return fetchExactCommitRangeDiff(client, repository, baseRevision, headRevision);
        } catch (IOException e) {
            throw new IllegalStateException("Unable to fetch exact pull-request diff", e);
        }
    }

    private static void requireExactRevision(String revision, String coordinate) {
        if (revision == null || !EXACT_REVISION.matcher(revision).matches()) {
            throw new IllegalArgumentException(coordinate + " revision is not an exact lowercase SHA");
        }
    }

    private Map<String, String> resolveTaskContext(
            Project project,
            String sourceBranch,
            String title,
            String description) {
        return taskContextEnrichmentService != null
                ? taskContextEnrichmentService.resolveTaskContext(project, sourceBranch, title, description)
                : Collections.emptyMap();
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
            String rawDiff) {
        return pullRequestData(title, description, rawDiff, null);
    }

    protected final PullRequestData pullRequestData(
            String title,
            String description,
            String rawDiff,
            String baseRevision) {
        return new PullRequestData(title, description, rawDiff, baseRevision);
    }

    protected final PullRequestMetadata pullRequestMetadata(
            String title,
            String description,
            String baseSha,
            String headSha,
            String mergeBaseSha) {
        return new PullRequestMetadata(
                title, description, baseSha, headSha, mergeBaseSha);
    }

    protected record RepositoryInfo(
            VcsConnection connection,
            String workspace,
            String repoSlug) {}

    protected record PullRequestData(
            String title,
            String description,
            String rawDiff,
            String baseRevision) {
        static PullRequestData empty() {
            return new PullRequestData(null, null, null, null);
        }
    }

    protected record PullRequestMetadata(
            String title,
            String description,
            String baseSha,
            String headSha,
            String mergeBaseSha) {}
}
