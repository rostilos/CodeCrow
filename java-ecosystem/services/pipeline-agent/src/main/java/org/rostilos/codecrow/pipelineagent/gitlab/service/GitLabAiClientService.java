package org.rostilos.codecrow.pipelineagent.gitlab.service;

import com.fasterxml.jackson.databind.JsonNode;
import okhttp3.OkHttpClient;
import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisMode;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.ProjectVcsConnectionBinding;
import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
import org.rostilos.codecrow.core.model.vcs.config.gitlab.GitLabConfig;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequestImpl;
import org.rostilos.codecrow.analysisengine.dto.request.processor.AnalysisProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsAiClientService;
import org.rostilos.codecrow.analysisengine.util.DiffContentFilter;
import org.rostilos.codecrow.analysisengine.util.DiffParser;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.rostilos.codecrow.vcsclient.gitlab.actions.GetCommitRangeDiffAction;
import org.rostilos.codecrow.vcsclient.gitlab.actions.GetMergeRequestAction;
import org.rostilos.codecrow.vcsclient.gitlab.actions.GetMergeRequestDiffAction;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
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
     * Minimum delta diff size in characters to consider incremental analysis worthwhile.
     */
    private static final int MIN_DELTA_DIFF_SIZE = 500;

    private final TokenEncryptionService tokenEncryptionService;
    private final VcsClientProvider vcsClientProvider;

    public GitLabAiClientService(
            TokenEncryptionService tokenEncryptionService,
            VcsClientProvider vcsClientProvider
    ) {
        this.tokenEncryptionService = tokenEncryptionService;
        this.vcsClientProvider = vcsClientProvider;
    }

    @Override
    public EVcsProvider getProvider() {
        return EVcsProvider.GITLAB;
    }

    private record VcsInfo(VcsConnection vcsConnection, String namespace, String repoSlug) {}

    private VcsInfo getVcsInfo(Project project) {
        ProjectVcsConnectionBinding vcsBinding = project.getVcsBinding();
        if (vcsBinding != null && vcsBinding.getVcsConnection() != null) {
            return new VcsInfo(vcsBinding.getVcsConnection(), vcsBinding.getWorkspace(), vcsBinding.getRepoSlug());
        }

        VcsRepoBinding repoBinding = project.getVcsRepoBinding();
        if (repoBinding != null && repoBinding.getVcsConnection() != null) {
            log.debug("Using VcsRepoBinding for project {} as fallback", project.getId());
            return new VcsInfo(repoBinding.getVcsConnection(), repoBinding.getExternalNamespace(), repoBinding.getExternalRepoSlug());
        }

        throw new IllegalStateException("No VCS connection configured for project: " + project.getId());
    }

    @Override
    public AiAnalysisRequest buildAiAnalysisRequest(
            Project project,
            AnalysisProcessRequest request,
            Optional<CodeAnalysis> previousAnalysis
    ) throws GeneralSecurityException {
        switch (request.getAnalysisType()) {
            case BRANCH_ANALYSIS:
                return buildBranchAnalysisRequest(project, (BranchProcessRequest) request, previousAnalysis);
            default:
                return buildMrAnalysisRequest(project, (PrProcessRequest) request, previousAnalysis);
        }
    }

    private AiAnalysisRequest buildMrAnalysisRequest(
            Project project,
            PrProcessRequest request,
            Optional<CodeAnalysis> previousAnalysis
    ) throws GeneralSecurityException {
        VcsInfo vcsInfo = getVcsInfo(project);
        VcsConnection vcsConnection = vcsInfo.vcsConnection();
        AIConnection aiConnection = project.getAiBinding().getAiConnection();

        // Initialize variables
        List<String> changedFiles = Collections.emptyList();
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
                    request.getPullRequestId().intValue()
            );

            mrTitle = mrData.has("title") ? mrData.get("title").asText() : null;
            mrDescription = mrData.has("description") ? mrData.get("description").asText() : null;

            log.info("Fetched MR metadata: title='{}', description length={}",
                    mrTitle, mrDescription != null ? mrDescription.length() : 0);

            // Fetch full MR diff
            GetMergeRequestDiffAction diffAction = new GetMergeRequestDiffAction(client);
            String fetchedDiff = diffAction.getMergeRequestDiff(
                    vcsInfo.namespace(),
                    vcsInfo.repoSlug(),
                    request.getPullRequestId().intValue()
            );
            
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

            // Determine analysis mode: INCREMENTAL if we have previous analysis with different commit
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

            // Parse diff to extract changed files and code snippets
            String diffToParse = analysisMode == AnalysisMode.INCREMENTAL && deltaDiff != null ? deltaDiff : rawDiff;
            changedFiles = DiffParser.extractChangedFiles(diffToParse);
            diffSnippets = DiffParser.extractDiffSnippets(diffToParse, 20);

            log.info("Analysis mode: {}, extracted {} changed files, {} code snippets",
                    analysisMode, changedFiles.size(), diffSnippets.size());

        } catch (IOException e) {
            log.warn("Failed to fetch/parse MR metadata/diff for RAG context: {}", e.getMessage());
        }

        var builder = AiAnalysisRequestImpl.builder()
                .withProjectId(project.getId())
                .withPullRequestId(request.getPullRequestId())
                .withProjectAiConnection(aiConnection)
                .withProjectVcsConnectionBindingInfo(vcsInfo.namespace(), vcsInfo.repoSlug())
                .withProjectAiConnectionTokenDecrypted(tokenEncryptionService.decrypt(aiConnection.getApiKeyEncrypted()))
                .withUseLocalMcp(true)
                .withPreviousAnalysisData(previousAnalysis)
                .withMaxAllowedTokens(aiConnection.getTokenLimitation())
                .withAnalysisType(request.getAnalysisType())
                .withPrTitle(mrTitle)
                .withPrDescription(mrDescription)
                .withChangedFiles(changedFiles)
                .withDiffSnippets(diffSnippets)
                .withRawDiff(rawDiff)
                .withProjectMetadata(project.getWorkspace().getName(), project.getNamespace())
                .withTargetBranchName(request.targetBranchName)
                .withVcsProvider("gitlab")
                // Incremental analysis fields
                .withAnalysisMode(analysisMode)
                .withDeltaDiff(deltaDiff)
                .withPreviousCommitHash(previousCommitHash)
                .withCurrentCommitHash(currentCommitHash);
        
        addVcsCredentials(builder, vcsConnection);
        
        return builder.build();
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
            DiffContentFilter contentFilter
    ) {
        try {
            GetCommitRangeDiffAction rangeDiffAction = new GetCommitRangeDiffAction(client);
            String fetchedDeltaDiff = rangeDiffAction.getCommitRangeDiff(
                    vcsInfo.namespace(),
                    vcsInfo.repoSlug(),
                    baseCommit,
                    headCommit
            );
            
            return contentFilter.filterDiff(fetchedDeltaDiff);
        } catch (IOException e) {
            log.warn("Failed to fetch delta diff from {} to {}: {}", 
                    baseCommit.substring(0, Math.min(7, baseCommit.length())), 
                    headCommit.substring(0, Math.min(7, headCommit.length())), 
                    e.getMessage());
            return null;
        }
    }

    private AiAnalysisRequest buildBranchAnalysisRequest(
            Project project,
            BranchProcessRequest request,
            Optional<CodeAnalysis> previousAnalysis
    ) throws GeneralSecurityException {
        VcsInfo vcsInfo = getVcsInfo(project);
        VcsConnection vcsConnection = vcsInfo.vcsConnection();
        AIConnection aiConnection = project.getAiBinding().getAiConnection();

        var builder = AiAnalysisRequestImpl.builder()
                .withProjectId(project.getId())
                .withPullRequestId(null)
                .withProjectAiConnection(aiConnection)
                .withProjectVcsConnectionBindingInfo(vcsInfo.namespace(), vcsInfo.repoSlug())
                .withProjectAiConnectionTokenDecrypted(tokenEncryptionService.decrypt(aiConnection.getApiKeyEncrypted()))
                .withUseLocalMcp(true)
                .withPreviousAnalysisData(previousAnalysis)
                .withMaxAllowedTokens(aiConnection.getTokenLimitation())
                .withAnalysisType(request.getAnalysisType())
                .withProjectMetadata(project.getWorkspace().getName(), project.getNamespace());
        
        addVcsCredentials(builder, vcsConnection);
        
        return builder.build();
    }
    
    private void addVcsCredentials(AiAnalysisRequestImpl.Builder<?> builder, VcsConnection connection) 
            throws GeneralSecurityException {
        if (connection.getConnectionType() == EVcsConnectionType.APPLICATION && connection.getAccessToken() != null) {
            String accessToken = tokenEncryptionService.decrypt(connection.getAccessToken());
            builder.withAccessToken(accessToken);
        } else if ((connection.getConnectionType() == EVcsConnectionType.PERSONAL_TOKEN ||
                    connection.getConnectionType() == EVcsConnectionType.REPOSITORY_TOKEN) && 
                   connection.getConfiguration() instanceof GitLabConfig config) {
            builder.withAccessToken(config.accessToken());
        } else {
            log.warn("Unknown connection type for VCS credentials: {}", connection.getConnectionType());
        }
    }
}
