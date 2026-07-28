package org.rostilos.codecrow.pipelineagent.generic.service;

import java.io.IOException;
import java.security.GeneralSecurityException;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import okhttp3.OkHttpClient;
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
 * Template for provider-backed AI request construction. Subclasses implement
 * only remote VCS reads; all analysis policy and request assembly lives here.
 */
public abstract class AbstractVcsAiClientService implements VcsAiClientService {
    private static final int MINIMUM_TRUSTED_COMMIT_PREFIX_LENGTH = 12;
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

    protected abstract PullRequestData fetchPullRequest(
            OkHttpClient client,
            RepositoryInfo repository,
            long pullRequestId) throws IOException;

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

    private List<AiAnalysisRequest> buildPullRequestAnalysis(
            Project project,
            PrProcessRequest request,
            Optional<CodeAnalysis> previousAnalysis,
            List<CodeAnalysis> allPrAnalyses) throws GeneralSecurityException {
        RepositoryInfo repository = repositoryInfo(project);
        AIConnection aiConnection = project.getAiBinding().getAiConnection();
        String previousCommit = previousAnalysis.map(CodeAnalysis::getCommitHash).orElse(null);
        String currentCommit = request.getCommitHash();
        PullRequestData pullRequest;
        PreparedDiff preparedDiff = PreparedDiff.empty(previousCommit, currentCommit);

        log.info("Building pull request analysis: project={}, AI model={}, provider={}, connection={}",
                project.getId(), aiConnection.getAiModel(), aiConnection.getProviderKey(), aiConnection.getId());

        try {
            OkHttpClient client = vcsClientProvider.getHttpClient(repository.connection());
            pullRequest = fetchPullRequest(client, repository, request.getPullRequestId());
            if (!isFullGitObjectId(pullRequest.headCommit())
                    || !isFullGitObjectId(pullRequest.baseCommit())) {
                throw new IOException("VCS provider did not return immutable pull-request commits");
            }
            if (!matchesAuthoritativeBranch(
                    request.targetBranchName, pullRequest.targetBranch())) {
                throw new IOException("Pull-request target branch changed during acquisition: expected="
                        + request.targetBranchName + ", actual=" + pullRequest.targetBranch());
            }
            if (!matchesAuthoritativeBranch(
                    request.sourceBranchName, pullRequest.sourceBranch())) {
                throw new IOException("Pull-request source branch changed during acquisition: expected="
                        + request.sourceBranchName + ", actual=" + pullRequest.sourceBranch());
            }
            if (!matchesAuthoritativeCommit(currentCommit, pullRequest.headCommit())) {
                throw new IOException("Pull-request head changed during acquisition: expected="
                        + currentCommit + ", actual=" + pullRequest.headCommit());
            }
            currentCommit = pullRequest.headCommit();
            // Webhook payloads from some providers contain a stable abbreviation.
            // All downstream cache, RAG, prompt and persistence identities must use
            // the full object ID acquired from the provider for this exact PR.
            request.commitHash = currentCommit;
            String pinnedDiff = fetchCommitRangeDiff(
                    client, repository, pullRequest.baseCommit(), pullRequest.headCommit());
            preparedDiff = diffPreparationService.prepare(
                    project,
                    request.getPullRequestId(),
                    pinnedDiff,
                    previousCommit,
                    currentCommit,
                    (base, head) -> fetchCommitRangeDiff(client, repository, base, head));
        } catch (IOException e) {
            throw new IllegalStateException("Unable to acquire immutable pull-request snapshot", e);
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
        CapabilityEnrichment capabilityEnrichment = prepareCapabilityEnrichment(
                repository, branchRequest.getCommitHash(), safeChangedFiles, "direct push");
        PrEnrichmentDataDto enrichment = capabilityEnrichment.enrichment();

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
            throw new IllegalStateException("Unable to obtain a VCS client for " + operation + " enrichment", e);
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
            throw new IllegalStateException("Unable to fetch " + operation + " file contents", e);
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
            throw new IllegalStateException(
                    "Unable to select project capabilities and file policy at commit " + commit,
                    exception);
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

    static boolean isFullGitObjectId(String value) {
        return value != null && FULL_GIT_OBJECT_ID.matcher(value).matches();
    }

    static boolean matchesAuthoritativeCommit(String supplied, String authoritative) {
        if (!isFullGitObjectId(authoritative)) {
            return false;
        }
        if (supplied == null) {
            return true;
        }
        if (supplied.isBlank()) {
            return false;
        }
        if (supplied.equals(authoritative)) {
            return true;
        }
        return supplied.length() >= MINIMUM_TRUSTED_COMMIT_PREFIX_LENGTH
                && supplied.length() < authoritative.length()
                && authoritative.startsWith(supplied);
    }

    static boolean matchesAuthoritativeBranch(String supplied, String authoritative) {
        return supplied != null
                && authoritative != null
                && !supplied.isBlank()
                && !authoritative.isBlank()
                && supplied.equals(authoritative);
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
