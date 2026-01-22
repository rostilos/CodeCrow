package org.rostilos.codecrow.pipelineagent.bitbucket.service;

import okhttp3.OkHttpClient;
import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisMode;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequestImpl;
import org.rostilos.codecrow.analysisengine.dto.request.processor.AnalysisProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsAiClientService;
import org.rostilos.codecrow.analysisengine.util.DiffContentFilter;
import org.rostilos.codecrow.analysisengine.util.DiffParser;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions.GetCommitRangeDiffAction;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions.GetPullRequestAction;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions.GetPullRequestDiffAction;
import org.rostilos.codecrow.vcsclient.utils.VcsConnectionCredentialsExtractor;
import org.rostilos.codecrow.vcsclient.utils.VcsConnectionCredentialsExtractor.VcsConnectionCredentials;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.security.GeneralSecurityException;
import java.util.Collections;
import java.util.List;
import java.util.Optional;

@Service
public class BitbucketAiClientService implements VcsAiClientService {
    private static final Logger log = LoggerFactory.getLogger(BitbucketAiClientService.class);
    
    /**
     * Threshold for escalating from incremental to full analysis.
     * If delta diff is larger than this percentage of full diff, use full analysis.
     */
    private static final double INCREMENTAL_ESCALATION_THRESHOLD = 0.5;
    
    /**
     * Minimum delta diff size in characters to consider incremental analysis worthwhile.
     * Below this threshold, full analysis might be more effective.
     */
    private static final int MIN_DELTA_DIFF_SIZE = 500;

    private final TokenEncryptionService tokenEncryptionService;
    private final VcsClientProvider vcsClientProvider;
    private final VcsConnectionCredentialsExtractor credentialsExtractor;

    public BitbucketAiClientService(
            TokenEncryptionService tokenEncryptionService,
            VcsClientProvider vcsClientProvider
    ) {
        this.tokenEncryptionService = tokenEncryptionService;
        this.vcsClientProvider = vcsClientProvider;
        this.credentialsExtractor = new VcsConnectionCredentialsExtractor(tokenEncryptionService);
    }

    @Override
    public EVcsProvider getProvider() {
        return EVcsProvider.BITBUCKET_CLOUD;
    }

    /**
     * Helper class to hold VCS connection info.
     */
    private record VcsInfo(VcsConnection vcsConnection, String workspace, String repoSlug) {}

    /**
     * Get VCS info from the project using the unified accessor.
     */
    private VcsInfo getVcsInfo(Project project) {
        // Use unified method that prefers VcsRepoBinding over legacy vcsBinding
        var vcsInfo = project.getEffectiveVcsRepoInfo();
        if (vcsInfo != null && vcsInfo.getVcsConnection() != null) {
            return new VcsInfo(vcsInfo.getVcsConnection(), vcsInfo.getRepoWorkspace(), vcsInfo.getRepoSlug());
        }

        throw new IllegalStateException("No VCS connection configured for project: " + project.getId());
    }

    @Override
    public AiAnalysisRequest buildAiAnalysisRequest(
            Project project,
            AnalysisProcessRequest request,
            Optional<CodeAnalysis> previousAnalysis
    ) throws GeneralSecurityException {
        if(request.getAnalysisType() == AnalysisType.BRANCH_ANALYSIS){
            return buildBranchAnalysisRequest(project, (BranchProcessRequest) request, previousAnalysis);
        } else {
            return buildPrAnalysisRequest(project, (PrProcessRequest) request, previousAnalysis);
        }
    }

    public AiAnalysisRequest buildPrAnalysisRequest(
            Project project,
            PrProcessRequest request,
            Optional<CodeAnalysis> previousAnalysis
    ) throws GeneralSecurityException {
        VcsInfo vcsInfo = getVcsInfo(project);
        VcsConnection vcsConnection = vcsInfo.vcsConnection();
        AIConnection aiConnection = project.getAiBinding().getAiConnection();
        AIConnection projectAiConnection = project.getAiBinding().getAiConnection();

        // Initialize variables
        List<String> changedFiles = Collections.emptyList();
        List<String> diffSnippets = Collections.emptyList();
        String prTitle = null;
        String prDescription = null;
        String rawDiff = null;
        String deltaDiff = null;
        AnalysisMode analysisMode = AnalysisMode.FULL;
        String previousCommitHash = previousAnalysis.map(CodeAnalysis::getCommitHash).orElse(null);
        String currentCommitHash = request.getCommitHash();

        try {
            OkHttpClient client = vcsClientProvider.getHttpClient(vcsConnection);

            // Fetch PR metadata
            GetPullRequestAction prAction = new GetPullRequestAction(client);
            GetPullRequestAction.PullRequestMetadata prMetadata = prAction.getPullRequest(
                    vcsInfo.workspace(),
                    vcsInfo.repoSlug(),
                    String.valueOf(request.getPullRequestId())
            );

            prTitle = prMetadata.getTitle();
            prDescription = prMetadata.getDescription();

            log.info("Fetched PR metadata: title='{}', description length={}",
                    prTitle, prDescription != null ? prDescription.length() : 0);

            // Fetch full PR diff
            GetPullRequestDiffAction diffAction = new GetPullRequestDiffAction(client);
            String fetchedDiff = diffAction.getPullRequestDiff(
                    vcsInfo.workspace(),
                    vcsInfo.repoSlug(),
                    String.valueOf(request.getPullRequestId())
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
                            deltaDiff = null; // Don't send delta if not using incremental mode
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
            // For incremental mode, parse from delta diff; for full mode, from full diff
            String diffToParse = analysisMode == AnalysisMode.INCREMENTAL && deltaDiff != null ? deltaDiff : rawDiff;
            changedFiles = DiffParser.extractChangedFiles(diffToParse);
            diffSnippets = DiffParser.extractDiffSnippets(diffToParse, 20);

            log.info("Analysis mode: {}, extracted {} changed files, {} code snippets",
                    analysisMode, changedFiles.size(), diffSnippets.size());

        } catch (IOException e) {
            log.warn("Failed to fetch/parse PR metadata/diff for RAG context: {}", e.getMessage());
            // Continue without metadata - RAG will use fallback
        }

        var builder = AiAnalysisRequestImpl.builder()
                .withProjectId(project.getId())
                .withPullRequestId(request.getPullRequestId())
                .withProjectAiConnection(aiConnection)
                .withProjectVcsConnectionBindingInfo(vcsInfo.workspace(), vcsInfo.repoSlug())
                .withProjectAiConnectionTokenDecrypted(tokenEncryptionService.decrypt(projectAiConnection.getApiKeyEncrypted()))
                .withUseLocalMcp(true)
                .withPreviousAnalysisData(previousAnalysis)
                .withMaxAllowedTokens(aiConnection.getTokenLimitation())
                .withAnalysisType(request.getAnalysisType())
                .withPrTitle(prTitle)
                .withPrDescription(prDescription)
                .withChangedFiles(changedFiles)
                .withDiffSnippets(diffSnippets)
                .withRawDiff(rawDiff)
                .withProjectMetadata(project.getWorkspace().getName(), project.getNamespace())
                .withTargetBranchName(request.targetBranchName)
                .withVcsProvider("bitbucket_cloud")
                // Incremental analysis fields
                .withAnalysisMode(analysisMode)
                .withDeltaDiff(deltaDiff)
                .withPreviousCommitHash(previousCommitHash)
                .withCurrentCommitHash(currentCommitHash);
        
        // Add VCS credentials based on connection type
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
                    vcsInfo.workspace(),
                    vcsInfo.repoSlug(),
                    baseCommit,
                    headCommit
            );
            
            // Apply same content filter as full diff
            return contentFilter.filterDiff(fetchedDeltaDiff);
        } catch (IOException e) {
            log.warn("Failed to fetch delta diff from {} to {}: {}", 
                    baseCommit.substring(0, Math.min(7, baseCommit.length())), 
                    headCommit.substring(0, Math.min(7, headCommit.length())), 
                    e.getMessage());
            return null;
        }
    }

    public AiAnalysisRequest buildBranchAnalysisRequest(
            Project project,
            BranchProcessRequest request,
            Optional<CodeAnalysis> previousAnalysis
    ) throws GeneralSecurityException {
        VcsInfo vcsInfo = getVcsInfo(project);
        VcsConnection vcsConnection = vcsInfo.vcsConnection();
        AIConnection aiConnection = project.getAiBinding().getAiConnection();
        AIConnection projectAiConnection = project.getAiBinding().getAiConnection();

        var builder = AiAnalysisRequestImpl.builder()
                .withProjectId(project.getId())
                .withPullRequestId(null)
                .withProjectAiConnection(aiConnection)
                .withProjectVcsConnectionBindingInfo(vcsInfo.workspace(), vcsInfo.repoSlug())
                .withProjectAiConnectionTokenDecrypted(tokenEncryptionService.decrypt(projectAiConnection.getApiKeyEncrypted()))
                .withUseLocalMcp(true)
                .withPreviousAnalysisData(previousAnalysis)
                .withMaxAllowedTokens(aiConnection.getTokenLimitation())
                .withAnalysisType(request.getAnalysisType())
                .withTargetBranchName(request.getTargetBranchName())
                .withCurrentCommitHash(request.getCommitHash())
                .withProjectMetadata(project.getWorkspace().getName(), project.getNamespace())
                .withVcsProvider("bitbucket_cloud");
        
        addVcsCredentials(builder, vcsConnection);
        
        return builder.build();
    }
    
    /**
     * Add VCS credentials to the builder based on connection type.
     * For OAUTH_MANUAL: uses OAuth consumer key/secret from config
     * For APP: uses bearer token directly via accessToken field
     */
    private void addVcsCredentials(AiAnalysisRequestImpl.Builder<?> builder, VcsConnection connection) 
            throws GeneralSecurityException {
        VcsConnectionCredentials credentials = credentialsExtractor.extractCredentials(connection);
        if (VcsConnectionCredentialsExtractor.hasAccessToken(credentials)) {
            builder.withAccessToken(credentials.accessToken());
        } else if (VcsConnectionCredentialsExtractor.hasOAuthCredentials(credentials)) {
            builder.withProjectVcsConnectionCredentials(
                    credentials.oAuthClient(),
                    credentials.oAuthSecret()
            );
        } else {
            log.warn("No credentials available for VCS connection type: {}", connection.getConnectionType());
        }
    }
}
