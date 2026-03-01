package org.rostilos.codecrow.pipelineagent.gitlab.service;

import com.fasterxml.jackson.databind.JsonNode;
import okhttp3.OkHttpClient;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.PrEnrichmentDataDto;
import org.rostilos.codecrow.analysisengine.service.pr.PrFileEnrichmentService;
import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisMode;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequestImpl;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiRequestPreviousIssueDTO;
import org.rostilos.codecrow.analysisengine.dto.request.processor.AnalysisProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsAiClientService;
import org.rostilos.codecrow.analysisengine.util.DiffContentFilter;
import org.rostilos.codecrow.analysisengine.util.DiffParser;
import org.rostilos.codecrow.analysisengine.util.TokenEstimator;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.rostilos.codecrow.vcsclient.gitlab.actions.GetCommitRangeDiffAction;
import org.rostilos.codecrow.vcsclient.gitlab.actions.GetMergeRequestAction;
import org.rostilos.codecrow.vcsclient.gitlab.actions.GetMergeRequestDiffAction;
import org.rostilos.codecrow.vcsclient.utils.VcsConnectionCredentialsExtractor;
import org.rostilos.codecrow.vcsclient.utils.VcsConnectionCredentialsExtractor.VcsConnectionCredentials;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.security.GeneralSecurityException;
import java.util.Collections;
import java.util.List;
import java.util.Optional;

@Service
public class GitLabAiClientService implements VcsAiClientService {
    private static final Logger log = LoggerFactory.getLogger(GitLabAiClientService.class);

    /**
     * Threshold for escalating from incremental to full analysis.
     * If delta diff is larger than this percentage of full diff, use full analysis.
     */
    private static final double INCREMENTAL_ESCALATION_THRESHOLD = 0.5;

    /**
     * Minimum delta diff size in characters to consider incremental analysis
     * worthwhile.
     */
    private static final int MIN_DELTA_DIFF_SIZE = 500;

    private final TokenEncryptionService tokenEncryptionService;
    private final VcsClientProvider vcsClientProvider;
    private final VcsConnectionCredentialsExtractor credentialsExtractor;
    private final PrFileEnrichmentService enrichmentService;

    public GitLabAiClientService(
            TokenEncryptionService tokenEncryptionService,
            VcsClientProvider vcsClientProvider,
            @Autowired(required = false) PrFileEnrichmentService enrichmentService) {
        this.tokenEncryptionService = tokenEncryptionService;
        this.vcsClientProvider = vcsClientProvider;
        this.credentialsExtractor = new VcsConnectionCredentialsExtractor(tokenEncryptionService);
        this.enrichmentService = enrichmentService;
    }

    @Override
    public EVcsProvider getProvider() {
        return EVcsProvider.GITLAB;
    }

    private record VcsInfo(VcsConnection vcsConnection, String namespace, String repoSlug) {
    }

    private VcsInfo getVcsInfo(Project project) {
        // Use unified method that prefers VcsRepoBinding over legacy vcsBinding
        var vcsInfo = project.getEffectiveVcsRepoInfo();
        if (vcsInfo != null && vcsInfo.getVcsConnection() != null) {
            return new VcsInfo(vcsInfo.getVcsConnection(), vcsInfo.getRepoWorkspace(), vcsInfo.getRepoSlug());
        }

        throw new IllegalStateException("No VCS connection configured for project: " + project.getId());
    }

    @Override
    public List<AiAnalysisRequest> buildAiAnalysisRequests(
            Project project,
            AnalysisProcessRequest request,
            Optional<CodeAnalysis> previousAnalysis) throws GeneralSecurityException {
        return buildAiAnalysisRequests(project, request, previousAnalysis, List.of());
    }

    @Override
    public List<AiAnalysisRequest> buildAiAnalysisRequests(
            Project project,
            AnalysisProcessRequest request,
            Optional<CodeAnalysis> previousAnalysis,
            List<CodeAnalysis> allPrAnalyses) throws GeneralSecurityException {
        switch (request.getAnalysisType()) {
            case BRANCH_ANALYSIS:
                return List.of(buildBranchAnalysisRequestInternal(project, (BranchProcessRequest) request,
                        previousAnalysis, null, null));
            default:
                return buildMrAnalysisRequests(project, (PrProcessRequest) request, previousAnalysis, allPrAnalyses);
        }
    }

    private List<AiAnalysisRequest> buildMrAnalysisRequests(
            Project project,
            PrProcessRequest request,
            Optional<CodeAnalysis> previousAnalysis,
            List<CodeAnalysis> allPrAnalyses) throws GeneralSecurityException {
        VcsInfo vcsInfo = getVcsInfo(project);
        VcsConnection vcsConnection = vcsInfo.vcsConnection();
        AIConnection aiConnection = project.getAiBinding().getAiConnection();

        // CRITICAL: Log the AI connection being used for debugging
        log.info("Building MR analysis request for project={}, AI model={}, provider={}, aiConnectionId={}",
                project.getId(), aiConnection.getAiModel(), aiConnection.getProviderKey(), aiConnection.getId());

        // Initialize variables
        List<String> changedFiles = Collections.emptyList();
        List<String> deletedFiles = Collections.emptyList();
        List<String> diffSnippets = Collections.emptyList();
        String mrTitle = null;
        String mrDescription = null;
        String rawDiff = null;
        String deltaDiff = null;
        AnalysisMode analysisMode = AnalysisMode.FULL;
        String previousCommitHash = previousAnalysis.map(CodeAnalysis::getCommitHash).orElse(null);
        String currentCommitHash = request.getCommitHash();

        try {
            OkHttpClient client = vcsClientProvider.getHttpClient(vcsConnection);

            // Fetch MR metadata
            GetMergeRequestAction mrAction = new GetMergeRequestAction(client);
            JsonNode mrData = mrAction.getMergeRequest(
                    vcsInfo.namespace(),
                    vcsInfo.repoSlug(),
                    request.getPullRequestId().intValue());

            mrTitle = mrData.has("title") ? mrData.get("title").asText() : null;
            mrDescription = mrData.has("description") ? mrData.get("description").asText() : null;

            log.info("Fetched MR metadata: title='{}', description length={}",
                    mrTitle, mrDescription != null ? mrDescription.length() : 0);

            // Fetch full MR diff
            GetMergeRequestDiffAction diffAction = new GetMergeRequestDiffAction(client);
            String fetchedDiff = diffAction.getMergeRequestDiff(
                    vcsInfo.namespace(),
                    vcsInfo.repoSlug(),
                    request.getPullRequestId().intValue());

            // Apply content filter
            DiffContentFilter contentFilter = new DiffContentFilter();
            rawDiff = contentFilter.filterDiff(fetchedDiff);

            int originalSize = fetchedDiff != null ? fetchedDiff.length() : 0;
            int filteredSize = rawDiff != null ? rawDiff.length() : 0;

            if (originalSize != filteredSize) {
                log.info("Diff filtered: {} -> {} chars ({}% reduction)",
                        originalSize, filteredSize,
                        originalSize > 0 ? (100 - (filteredSize * 100 / originalSize)) : 0);
            }

            // Check token limit before proceeding with analysis
            int maxTokenLimit = project.getEffectiveConfig().maxAnalysisTokenLimit();
            TokenEstimator.TokenEstimationResult tokenEstimate = TokenEstimator.estimateAndCheck(rawDiff,
                    maxTokenLimit);
            log.info("Token estimation for MR diff: {}", tokenEstimate.toLogString());

            if (tokenEstimate.exceedsLimit()) {
                log.info(
                        "MR diff exceeds token limit, Map-Reduce Diff Chunking will be used. Project={}, PR={}, Tokens={}/{}",
                        project.getId(), request.getPullRequestId(),
                        tokenEstimate.estimatedTokens(), tokenEstimate.maxAllowedTokens());
            }

            // Determine analysis mode: INCREMENTAL if we have previous analysis with
            // different commit
            boolean canUseIncremental = previousAnalysis.isPresent()
                    && previousCommitHash != null
                    && currentCommitHash != null
                    && !previousCommitHash.equals(currentCommitHash);

            if (canUseIncremental) {
                // Try to fetch delta diff (changes since last analyzed commit)
                deltaDiff = fetchDeltaDiff(client, vcsInfo, previousCommitHash, currentCommitHash, contentFilter);

                if (deltaDiff != null && !deltaDiff.isEmpty()) {
                    // Check if delta is worth using (not too large compared to full diff)
                    int deltaSize = deltaDiff.length();
                    int fullSize = rawDiff != null ? rawDiff.length() : 0;

                    if (deltaSize >= MIN_DELTA_DIFF_SIZE && fullSize > 0) {
                        double deltaRatio = (double) deltaSize / fullSize;

                        if (deltaRatio <= INCREMENTAL_ESCALATION_THRESHOLD) {
                            analysisMode = AnalysisMode.INCREMENTAL;
                            log.info("Using INCREMENTAL analysis mode: delta={} chars ({}% of full diff {})",
                                    deltaSize, Math.round(deltaRatio * 100), fullSize);
                        } else {
                            log.info("Escalating to FULL analysis: delta too large ({}% of full diff)",
                                    Math.round(deltaRatio * 100));
                            deltaDiff = null;
                        }
                    } else if (deltaSize < MIN_DELTA_DIFF_SIZE) {
                        log.info("Delta diff too small ({} chars), using FULL analysis", deltaSize);
                        deltaDiff = null;
                    }
                } else {
                    log.info("Could not fetch delta diff, using FULL analysis");
                }
            } else {
                log.info("Using FULL analysis mode (first analysis or same commit)");
            }

            // Parse diff to extract changed files
            String diffToParse = analysisMode == AnalysisMode.INCREMENTAL && deltaDiff != null ? deltaDiff : rawDiff;
            changedFiles = DiffParser.extractChangedFiles(diffToParse);
            deletedFiles = DiffParser.extractDeletedFiles(diffToParse);
            diffSnippets = Collections.emptyList(); // Phase 5: Smart Context Window Management

            log.info("Analysis mode: {}, extracted {} changed files, {} deleted files, {} code snippets",
                    analysisMode, changedFiles.size(), deletedFiles.size(), diffSnippets.size());

        } catch (IOException e) {
            log.warn("Failed to fetch/parse MR metadata/diff for RAG context: {}", e.getMessage());
        }

        // Enrich PR with full file contents and dependency graph
        PrEnrichmentDataDto enrichmentData = PrEnrichmentDataDto.empty();
        if (enrichmentService != null && enrichmentService.isEnrichmentEnabled() && !changedFiles.isEmpty()) {
            try {
                VcsClient vcsClient = vcsClientProvider.getClient(vcsConnection);
                enrichmentData = enrichmentService.enrichPrFiles(
                        vcsClient,
                        vcsInfo.namespace(),
                        vcsInfo.repoSlug(),
                        request.getSourceBranchName(),
                        changedFiles);
                log.info("PR enrichment completed: {} files enriched, {} relationships",
                        enrichmentData.stats().filesEnriched(),
                        enrichmentData.stats().relationshipsFound());
            } catch (Exception e) {
                log.warn("Failed to enrich MR files (non-critical): {}", e.getMessage());
            }
        }

        // Build a single analysis request with the FULL diff.
        // Token-safe batching (splitting files into LLM-sized batches)
        // is handled by the Python multi-stage pipeline's Stage 1.
        String diffForAnalysis = analysisMode == AnalysisMode.INCREMENTAL && deltaDiff != null ? deltaDiff : rawDiff;

        AiAnalysisRequestImpl.Builder<?> builder = AiAnalysisRequestImpl.builder()
                .withProjectId(project.getId())
                .withPullRequestId(request.getPullRequestId())
                .withProjectAiConnection(aiConnection)
                .withProjectVcsConnectionBindingInfo(vcsInfo.namespace(), vcsInfo.repoSlug())
                .withProjectAiConnectionTokenDecrypted(
                        tokenEncryptionService.decrypt(aiConnection.getApiKeyEncrypted()))
                .withUseLocalMcp(true)
                .withUseMcpTools(project.getEffectiveConfig().useMcpTools())
                .withAllPrAnalysesData(allPrAnalyses)
                .withMaxAllowedTokens(project.getEffectiveConfig().maxAnalysisTokenLimit())
                .withAnalysisType(request.getAnalysisType())
                .withPrTitle(mrTitle)
                .withPrDescription(mrDescription)
                .withChangedFiles(changedFiles)
                .withDeletedFiles(deletedFiles)
                .withDiffSnippets(Collections.emptyList())
                .withRawDiff(rawDiff)
                .withProjectMetadata(project.getWorkspace().getName(), project.getNamespace())
                .withTargetBranchName(request.targetBranchName)
                .withVcsProvider("gitlab")
                .withAnalysisMode(analysisMode)
                .withDeltaDiff(analysisMode == AnalysisMode.INCREMENTAL ? diffForAnalysis : null)
                .withPreviousCommitHash(previousCommitHash)
                .withCurrentCommitHash(currentCommitHash)
                .withEnrichmentData(enrichmentData)
                .withProjectRules(project.getEffectiveConfig().getProjectRulesConfig().toEnabledRulesJson());

        addVcsCredentials(builder, vcsConnection);
        return Collections.singletonList(builder.build());
    }

    /**
     * Fetches the delta diff between two commits.
     * Returns null if fetching fails.
     */
    private String fetchDeltaDiff(
            OkHttpClient client,
            VcsInfo vcsInfo,
            String baseCommit,
            String headCommit,
            DiffContentFilter contentFilter) {
        try {
            GetCommitRangeDiffAction rangeDiffAction = new GetCommitRangeDiffAction(client);
            String fetchedDeltaDiff = rangeDiffAction.getCommitRangeDiff(
                    vcsInfo.namespace(),
                    vcsInfo.repoSlug(),
                    baseCommit,
                    headCommit);

            return contentFilter.filterDiff(fetchedDeltaDiff);
        } catch (IOException e) {
            log.warn("Failed to fetch delta diff from {} to {}: {}",
                    baseCommit.substring(0, Math.min(7, baseCommit.length())),
                    headCommit.substring(0, Math.min(7, headCommit.length())),
                    e.getMessage());
            return null;
        }
    }

    @Override
    public List<AiAnalysisRequest> buildAiAnalysisRequestsForBranchReconciliation(
            Project project,
            AnalysisProcessRequest request,
            List<AiRequestPreviousIssueDTO> previousIssues,
            java.util.Map<String, String> fileContents) throws GeneralSecurityException {
        BranchProcessRequest branchReq = (BranchProcessRequest) request;
        return List.of(buildBranchAnalysisRequestInternal(project, branchReq, null, previousIssues, fileContents));
    }

    @Override
    public List<AiAnalysisRequest> buildDirectPushAnalysisRequests(
            Project project,
            AnalysisProcessRequest request,
            String rawDiff,
            java.util.Map<String, String> fileContents,
            java.util.List<String> changedFiles) throws GeneralSecurityException {
        BranchProcessRequest branchReq = (BranchProcessRequest) request;
        return List.of(buildDirectPushAnalysisRequestInternal(project, branchReq, rawDiff, fileContents, changedFiles));
    }

    /**
     * Internal builder for branch analysis requests.
     * Accepts EITHER a CodeAnalysis entity OR pre-built DTOs for previous issues.
     * When {@code previousIssueDTOs} is non-null it takes precedence (avoids lazy
     * proxy access).
     */
    private AiAnalysisRequest buildBranchAnalysisRequestInternal(
            Project project,
            BranchProcessRequest request,
            Optional<CodeAnalysis> previousAnalysis,
            List<AiRequestPreviousIssueDTO> previousIssueDTOs,
            java.util.Map<String, String> fileContents) throws GeneralSecurityException {
        VcsInfo vcsInfo = getVcsInfo(project);
        VcsConnection vcsConnection = vcsInfo.vcsConnection();
        AIConnection aiConnection = project.getAiBinding().getAiConnection();

        var builder = AiAnalysisRequestImpl.builder()
                .withProjectId(project.getId())
                .withPullRequestId(null)
                .withProjectAiConnection(aiConnection)
                .withProjectVcsConnectionBindingInfo(vcsInfo.namespace(), vcsInfo.repoSlug())
                .withProjectAiConnectionTokenDecrypted(
                        tokenEncryptionService.decrypt(aiConnection.getApiKeyEncrypted()))
                .withUseLocalMcp(true)
                .withUseMcpTools(project.getEffectiveConfig().useMcpTools())
                .withMaxAllowedTokens(project.getEffectiveConfig().maxAnalysisTokenLimit())
                .withAnalysisType(request.getAnalysisType())
                .withTargetBranchName(request.getTargetBranchName())
                .withCurrentCommitHash(request.getCommitHash())
                .withProjectMetadata(project.getWorkspace().getName(), project.getNamespace())
                .withVcsProvider("gitlab")
                .withProjectRules(project.getEffectiveConfig().getProjectRulesConfig().toEnabledRulesJson());

        // Use pre-built DTOs when available (branch reconciliation path — no lazy
        // proxies);
        // otherwise fall back to entity-based conversion.
        if (previousIssueDTOs != null && !previousIssueDTOs.isEmpty()) {
            builder.withPreviousIssues(previousIssueDTOs);
        } else if (previousAnalysis != null) {
            builder.withPreviousAnalysisData(previousAnalysis);
        }

        addVcsCredentials(builder, vcsConnection);

        if (fileContents != null && !fileContents.isEmpty()) {
            builder.withReconciliationFileContents(fileContents);
        }

        return builder.build();
    }

    /**
     * Builds a full AI analysis request for direct push (hybrid branch analysis).
     * Unlike the reconciliation variant, this includes the raw diff, changed files,
     * and enrichment data — producing a PR-like analysis from a commit range.
     */
    private AiAnalysisRequest buildDirectPushAnalysisRequestInternal(
            Project project,
            BranchProcessRequest request,
            String rawDiff,
            java.util.Map<String, String> fileContents,
            java.util.List<String> changedFiles) throws GeneralSecurityException {
        VcsInfo vcsInfo = getVcsInfo(project);
        VcsConnection vcsConnection = vcsInfo.vcsConnection();
        AIConnection aiConnection = project.getAiBinding().getAiConnection();

        log.info("Building direct push analysis request for project={}, branch={}, {} changed files",
                project.getId(), request.getTargetBranchName(),
                changedFiles != null ? changedFiles.size() : 0);

        // Compute deleted files from diff
        List<String> deletedFiles = DiffParser.extractDeletedFiles(rawDiff != null ? rawDiff : "");
        List<String> diffSnippets = DiffParser.extractDiffSnippets(rawDiff != null ? rawDiff : "", 20);

        // Enrich with AST metadata if enrichment service is available
        PrEnrichmentDataDto enrichmentData = PrEnrichmentDataDto.empty();
        if (enrichmentService != null && enrichmentService.isEnrichmentEnabled()
                && changedFiles != null && !changedFiles.isEmpty()) {
            try {
                VcsClient vcsClient = vcsClientProvider.getClient(vcsConnection);
                enrichmentData = enrichmentService.enrichPrFiles(
                        vcsClient,
                        vcsInfo.namespace(),
                        vcsInfo.repoSlug(),
                        request.getTargetBranchName(),
                        changedFiles);
                log.info("Direct push enrichment completed: {} files enriched, {} relationships",
                        enrichmentData.stats().filesEnriched(),
                        enrichmentData.stats().relationshipsFound());
            } catch (Exception e) {
                log.warn("Failed to enrich direct push files (non-critical): {}", e.getMessage());
            }
        }

        var builder = AiAnalysisRequestImpl.builder()
                .withProjectId(project.getId())
                .withPullRequestId(null)
                .withProjectAiConnection(aiConnection)
                .withProjectVcsConnectionBindingInfo(vcsInfo.namespace(), vcsInfo.repoSlug())
                .withProjectAiConnectionTokenDecrypted(
                        tokenEncryptionService.decrypt(aiConnection.getApiKeyEncrypted()))
                .withUseLocalMcp(true)
                .withUseMcpTools(project.getEffectiveConfig().useMcpTools())
                .withMaxAllowedTokens(project.getEffectiveConfig().maxAnalysisTokenLimit())
                .withAnalysisType(request.getAnalysisType())
                .withTargetBranchName(request.getTargetBranchName())
                .withCurrentCommitHash(request.getCommitHash())
                .withProjectMetadata(project.getWorkspace().getName(), project.getNamespace())
                .withVcsProvider("gitlab")
                .withProjectRules(project.getEffectiveConfig().getProjectRulesConfig().toEnabledRulesJson())
                // PR-like analysis fields built from commit range
                .withChangedFiles(changedFiles != null ? changedFiles : Collections.emptyList())
                .withDeletedFiles(deletedFiles)
                .withDiffSnippets(diffSnippets)
                .withRawDiff(rawDiff)
                .withAnalysisMode(org.rostilos.codecrow.core.model.codeanalysis.AnalysisMode.FULL)
                .withEnrichmentData(enrichmentData);

        addVcsCredentials(builder, vcsConnection);

        return builder.build();
    }

    private void addVcsCredentials(AiAnalysisRequestImpl.Builder<?> builder, VcsConnection connection)
            throws GeneralSecurityException {
        VcsConnectionCredentials credentials = credentialsExtractor.extractCredentials(connection);
        if (VcsConnectionCredentialsExtractor.hasAccessToken(credentials)) {
            builder.withAccessToken(credentials.accessToken());
        } else {
            log.warn("No access token available for VCS connection type: {}", connection.getConnectionType());
        }
    }
}
