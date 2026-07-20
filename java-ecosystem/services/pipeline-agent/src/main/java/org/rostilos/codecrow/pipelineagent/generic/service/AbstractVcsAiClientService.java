package org.rostilos.codecrow.pipelineagent.generic.service;

import java.io.IOException;
import java.security.GeneralSecurityException;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.regex.Pattern;

import okhttp3.OkHttpClient;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequestImpl;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiRequestPreviousIssueDTO;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AgenticRepositoryArchive;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.PrEnrichmentDataDto;
import org.rostilos.codecrow.analysisengine.dto.request.processor.AnalysisProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.analysisengine.service.pr.PrFileEnrichmentService;
import org.rostilos.codecrow.analysisengine.service.pr.PullRequestDiffPreparationService;
import org.rostilos.codecrow.analysisengine.service.pr.PullRequestDiffPreparationService.PreparedDiff;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsAiClientService;
import org.rostilos.codecrow.analysisengine.util.DiffParser;
import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisMode;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.ReviewApproach;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.pipelineagent.agentic.AgenticRepositoryArchiveService;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.rostilos.codecrow.vcsclient.utils.VcsConnectionCredentialsExtractor;
import org.rostilos.codecrow.vcsclient.utils.VcsConnectionCredentialsExtractor.VcsConnectionCredentials;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;

/**
 * Template for provider-backed AI request construction. Subclasses implement
 * only remote VCS reads; all analysis policy and request assembly lives here.
 */
public abstract class AbstractVcsAiClientService implements VcsAiClientService {
    private static final Pattern EXACT_REVISION =
            Pattern.compile("(?:[0-9a-f]{40}|[0-9a-f]{64})");

    private final Logger log = LoggerFactory.getLogger(getClass());
    private final TokenEncryptionService tokenEncryptionService;
    private final VcsClientProvider vcsClientProvider;
    private final VcsConnectionCredentialsExtractor credentialsExtractor;
    private final PrFileEnrichmentService enrichmentService;
    private final TaskContextEnrichmentService taskContextEnrichmentService;
    private final TaskHistoryContextService taskHistoryContextService;
    private final PullRequestDiffPreparationService diffPreparationService;
    private AgenticRepositoryArchiveService agenticRepositoryArchiveService;

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

    @Autowired(required = false)
    protected void setAgenticRepositoryArchiveService(
            AgenticRepositoryArchiveService agenticRepositoryArchiveService) {
        this.agenticRepositoryArchiveService = agenticRepositoryArchiveService;
    }

    @Override
    public void discardUndispatchedAiAnalysisRequest(AiAnalysisRequest request) {
        if (request == null || request.getAgenticRepository() == null
                || agenticRepositoryArchiveService == null) {
            return;
        }
        try {
            agenticRepositoryArchiveService.cleanup(
                    request.getAgenticRepository().workspaceKey());
        } catch (IOException cleanupFailure) {
            log.warn("Unable to clean up undispatched AGENTIC archive: {}",
                    cleanupFailure.getMessage());
        }
    }

    protected abstract PullRequestData fetchPullRequest(
            OkHttpClient client,
            RepositoryInfo repository,
            long pullRequestId) throws IOException;

    /** Reads title and immutable revisions without relying on a mutable PR diff. */
    protected PullRequestMetadata fetchPullRequestMetadata(
            OkHttpClient client,
            RepositoryInfo repository,
            long pullRequestId) throws IOException {
        throw new IOException(
                "Exact pull-request metadata is unavailable for " + getProvider());
    }

    /** Reads only the current mutable PR head for the post-analysis guard. */
    protected String fetchPullRequestHead(
            OkHttpClient client,
            RepositoryInfo repository,
            long pullRequestId) throws IOException {
        return fetchPullRequestMetadata(client, repository, pullRequestId).headSha();
    }

    protected abstract String fetchCommitRangeDiff(
            OkHttpClient client,
            RepositoryInfo repository,
            String baseCommit,
            String headCommit) throws IOException;

    @Override
    public final boolean isPullRequestHeadCurrent(
            Project project,
            AiAnalysisRequest request) throws GeneralSecurityException, IOException {
        if (request == null || request.getReviewApproach() != ReviewApproach.AGENTIC) {
            return true;
        }
        if (request.getPullRequestId() == null
                || request.getAgenticRepository() == null) {
            return false;
        }

        String analyzedHead = requireExactRevision(
                request.getCurrentCommitHash(), "analyzed head");
        if (!analyzedHead.equals(request.getAgenticRepository().snapshotSha())) {
            return false;
        }

        RepositoryInfo repository = repositoryInfo(project);
        OkHttpClient client = vcsClientProvider.getHttpClient(repository.connection());
        String currentHead = requireExactRevision(
                fetchPullRequestHead(client, repository, request.getPullRequestId()),
                "current pull-request head");
        return analyzedHead.equals(currentHead);
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
        if (project.getEffectiveConfig().reviewApproach() == ReviewApproach.AGENTIC) {
            return buildAgenticPullRequestAnalysis(
                    project, (PrProcessRequest) request);
        }
        return buildPullRequestAnalysis(
                project, (PrProcessRequest) request, previousAnalysis, allPrAnalyses);
    }

    private List<AiAnalysisRequest> buildAgenticPullRequestAnalysis(
            Project project,
            PrProcessRequest request) throws GeneralSecurityException {
        RepositoryInfo repository = repositoryInfo(project);
        AIConnection aiConnection = project.getAiBinding().getAiConnection();
        String acceptedHead = requireExactRevision(
                request.getCommitHash(), "webhook head");

        OkHttpClient client = vcsClientProvider.getHttpClient(repository.connection());
        PullRequestMetadata metadata;
        String exactDiff;
        try {
            metadata = fetchPullRequestMetadata(
                    client, repository, request.getPullRequestId());
            if (metadata == null) {
                throw new IOException("Pull-request metadata is missing");
            }
            requireExactRevision(metadata.baseSha(), "base");
            requireExactRevision(metadata.headSha(), "head");
            requireExactRevision(metadata.mergeBaseSha(), "merge base");
            if (!acceptedHead.equals(metadata.headSha())) {
                throw new IllegalStateException(
                        "Webhook head no longer matches the pull-request head");
            }
            exactDiff = fetchCommitRangeDiff(
                    client, repository, metadata.mergeBaseSha(), metadata.headSha());
            ExactDiffIntegrityValidator.validate(metadata, exactDiff);
        } catch (IOException failure) {
            throw new IllegalStateException(
                    "Unable to acquire exact AGENTIC pull-request input", failure);
        }

        PreparedDiff preparedDiff = diffPreparationService.prepareAgenticExact(
                project,
                request.getPullRequestId(),
                exactDiff,
                metadata.mergeBaseSha(),
                metadata.headSha());
        if (preparedDiff.isEmpty()) {
            log.info("Skipping AGENTIC analysis because no changed files match scope: project={}, PR={}",
                    project.getId(), request.getPullRequestId());
            return List.of();
        }

        Map<String, String> taskContext = resolveTaskContext(
                project,
                request.sourceBranchName,
                metadata.title(),
                metadata.description());
        String taskHistory = resolveTaskHistory(
                project, request, taskContext, metadata.title(), metadata.description());

        AiAnalysisRequestImpl.Builder<?> builder = baseBuilder(
                project, request, repository, aiConnection)
                .withReviewApproach(ReviewApproach.AGENTIC)
                .withUseLocalMcp(false)
                .withUseMcpTools(false)
                .withPullRequestId(request.getPullRequestId())
                .withPrTitle(metadata.title())
                .withPrDescription(metadata.description())
                .withTaskContext(taskContext)
                .withTaskHistoryContext(taskHistory)
                .withChangedFiles(preparedDiff.changedFiles())
                .withDeletedFiles(preparedDiff.deletedFiles())
                .withDiffSnippets(List.of())
                .withRawDiff(preparedDiff.fullDiff())
                .withTargetBranchName(request.targetBranchName)
                .withSourceBranchName(request.sourceBranchName)
                .withAnalysisMode(AnalysisMode.FULL)
                .withDeltaDiff(null)
                .withPreviousCommitHash(metadata.mergeBaseSha())
                .withCurrentCommitHash(metadata.headSha());
        AgenticRepositoryArchive archive = stageAgenticRepository(
                project, request, repository, metadata.headSha());
        try {
            return List.of(builder.withAgenticRepository(archive).build());
        } catch (RuntimeException failure) {
            cleanupArchiveAfterBuildFailure(archive, failure);
            throw failure;
        }
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
                .withPreviousCommitHash(previousCommit)
                .withCurrentCommitHash(currentCommit)
                .withEnrichmentData(enrichment);

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
        return AiAnalysisRequestImpl.builder()
                .withProjectId(project.getId())
                .withProjectAiConnection(aiConnection)
                .withProjectVcsConnectionBindingInfo(repository.workspace(), repository.repoSlug())
                .withProjectAiConnectionTokenDecrypted(
                        tokenEncryptionService.decrypt(aiConnection.getApiKeyEncrypted()))
                .withUseLocalMcp(true)
                .withUseMcpTools(project.getEffectiveConfig().useMcpTools())
                .withMaxAllowedTokens(project.getEffectiveConfig().maxAnalysisTokenLimit())
                .withAnalysisType(request.getAnalysisType())
                .withProjectMetadata(project.getWorkspace().getName(), project.getNamespace())
                .withVcsProvider(providerKey())
                .withProjectRules(project.getEffectiveConfig().getProjectRulesConfig().toEnabledRulesJson());
    }

    private AgenticRepositoryArchive stageAgenticRepository(
            Project project,
            PrProcessRequest request,
            RepositoryInfo repository,
            String exactHead) {
        if (agenticRepositoryArchiveService == null) {
            throw new IllegalStateException(
                    "AGENTIC review requires repository archive staging");
        }
        VcsClient vcsClient = vcsClientProvider.getClient(repository.connection());
        try {
            AgenticRepositoryArchive archive = agenticRepositoryArchiveService.stage(
                    vcsClient,
                    project.getId() + ":" + request.getPullRequestId() + ":" + UUID.randomUUID(),
                    repository.workspace(),
                    repository.repoSlug(),
                    exactHead);
            if (archive == null || !exactHead.equals(archive.snapshotSha())) {
                IllegalStateException failure = new IllegalStateException(
                        "Staged AGENTIC archive does not match the pull-request head");
                cleanupArchiveAfterBuildFailure(archive, failure);
                throw failure;
            }
            return archive;
        } catch (IOException failure) {
            throw new IllegalStateException(
                    "Unable to stage AGENTIC repository archive", failure);
        }
    }

    private void cleanupArchiveAfterBuildFailure(
            AgenticRepositoryArchive archive,
            RuntimeException failure) {
        if (archive == null || agenticRepositoryArchiveService == null) {
            return;
        }
        try {
            agenticRepositoryArchiveService.cleanup(archive.workspaceKey());
        } catch (IOException cleanupFailure) {
            failure.addSuppressed(cleanupFailure);
        }
    }

    private static String requireExactRevision(String revision, String coordinate) {
        if (revision == null || !EXACT_REVISION.matcher(revision).matches()) {
            throw new IllegalArgumentException(
                    coordinate + " must be an exact lowercase commit SHA");
        }
        return revision;
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
        return new PullRequestData(title, description, rawDiff);
    }

    protected final PullRequestMetadata pullRequestMetadata(
            String title,
            String description,
            String baseSha,
            String headSha,
            String mergeBaseSha) {
        return new PullRequestMetadata(
                title, description, baseSha, headSha, mergeBaseSha, false, List.of());
    }

    protected final PullRequestMetadata pullRequestMetadata(
            String title,
            String description,
            String baseSha,
            String headSha,
            String mergeBaseSha,
            List<ExpectedFileChange> expectedFileChanges) {
        return new PullRequestMetadata(
                title, description, baseSha, headSha, mergeBaseSha,
                true,
                expectedFileChanges != null ? List.copyOf(expectedFileChanges) : List.of());
    }

    protected final ExpectedFileChange expectedFileChange(
            String path,
            long additions,
            long deletions) {
        return new ExpectedFileChange(path, additions, deletions);
    }

    protected record RepositoryInfo(
            VcsConnection connection,
            String workspace,
            String repoSlug) {}

    protected record PullRequestData(
            String title,
            String description,
            String rawDiff) {
        static PullRequestData empty() {
            return new PullRequestData(null, null, null);
        }
    }

    protected record PullRequestMetadata(
            String title,
            String description,
            String baseSha,
            String headSha,
            String mergeBaseSha,
            boolean exactInventoryAvailable,
            List<ExpectedFileChange> expectedFileChanges) {
    }

    protected record ExpectedFileChange(
            String path,
            long additions,
            long deletions) {
        protected ExpectedFileChange {
            if (path == null || path.isBlank() || additions < 0 || deletions < 0) {
                throw new IllegalArgumentException("Invalid expected file change");
            }
        }
    }

}
