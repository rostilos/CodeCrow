package org.rostilos.codecrow.pipelineagent.generic.service;

import java.io.IOException;
import java.security.GeneralSecurityException;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequestImpl;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiRequestPreviousIssueDTO;
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
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
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
        String previousCommit = previousAnalysis.map(CodeAnalysis::getCommitHash).orElse(null);
        String currentCommit = request.getCommitHash();
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
            } catch (IOException metadataError) {
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

            String diff;
            if (hasText(pullRequest.baseCommit()) && hasText(pullRequest.headCommit())) {
                try {
                    diff = client.getCommitRangeDiff(
                            repository.workspace(), repository.repoSlug(),
                            pullRequest.baseCommit(), pullRequest.headCommit());
                } catch (IOException rangeError) {
                    log.warn("Commit-range PR diff failed for project={}, PR={}; "
                                    + "using provider-native PR diff: {}",
                            project.getId(), request.getPullRequestId(), rangeError.getMessage());
                    diff = client.getPullRequestDiff(
                            repository.workspace(), repository.repoSlug(),
                            request.getPullRequestId());
                }
            } else {
                log.warn("PR metadata did not include both commit IDs for project={}, PR={}; "
                                + "using provider-native PR diff",
                        project.getId(), request.getPullRequestId());
                diff = client.getPullRequestDiff(
                        repository.workspace(), repository.repoSlug(),
                        request.getPullRequestId());
            }

            preparedDiff = diffPreparationService.prepare(
                    project,
                    request.getPullRequestId(),
                    diff,
                    previousCommit,
                    currentCommit,
                    (base, head) -> client.getCommitRangeDiff(
                            repository.workspace(), repository.repoSlug(), base, head));
        } catch (IOException e) {
            throw new IllegalStateException(
                    "Unable to fetch pull-request changes from the VCS provider: " + e.getMessage(), e);
        }

        if (preparedDiff.isEmpty()) {
            log.info("Skipping analysis because no changed files match the project scope: project={}, PR={}",
                    project.getId(), request.getPullRequestId());
            return List.of();
        }

        CapabilityEnrichment capabilityEnrichment = prepareCapabilityEnrichment(
                repository, currentCommit, preparedDiff.changedFiles(), "pull request");
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
                .withDiffSnippets(List.of())
                .withRawDiff(preparedDiff.fullDiff())
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
                .withProjectAiConnection(aiConnection)
                .withProjectVcsConnectionBindingInfo(repository.workspace(), repository.repoSlug())
                .withProjectAiConnectionTokenDecrypted(
                        tokenEncryptionService.decrypt(aiConnection.getApiKeyEncrypted()))
                .withUseLocalMcp(true)
                .withUseMcpTools(effectiveConfig.useMcpTools())
                .withRagEnabled(ragConfig != null && ragConfig.enabled())
                .withMaxAllowedTokens(effectiveConfig.maxAnalysisTokenLimit())
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
                    enrichFiles(repository, commit, changedFiles, operation),
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
            return new CapabilityEnrichment(enrichment, capabilities);
        } catch (Exception exception) {
            log.warn("Skipping project capability enrichment at commit {}: {}",
                    commit, exception.getMessage());
            return new CapabilityEnrichment(
                    enrichFiles(repository, commit, changedFiles, operation),
                    null);
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
