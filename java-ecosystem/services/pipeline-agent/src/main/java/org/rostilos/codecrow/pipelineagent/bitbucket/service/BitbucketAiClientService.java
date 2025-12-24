package org.rostilos.codecrow.pipelineagent.bitbucket.service;

import okhttp3.OkHttpClient;
import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.ProjectVcsConnectionBinding;
import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
import org.rostilos.codecrow.core.model.vcs.config.cloud.BitbucketCloudConfig;
import org.rostilos.codecrow.pipelineagent.bitbucket.dto.request.ai.AiBranchAnalysisRequest;
import org.rostilos.codecrow.pipelineagent.bitbucket.dto.request.ai.AiPullRequestAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.AnalysisProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsAiClientService;
import org.rostilos.codecrow.analysisengine.util.DiffParser;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions.GetPullRequestAction;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions.GetPullRequestDiffAction;
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

    private final TokenEncryptionService tokenEncryptionService;
    private final VcsClientProvider vcsClientProvider;

    public BitbucketAiClientService(
            TokenEncryptionService tokenEncryptionService,
            VcsClientProvider vcsClientProvider
    ) {
        this.tokenEncryptionService = tokenEncryptionService;
        this.vcsClientProvider = vcsClientProvider;
    }

    @Override
    public EVcsProvider getProvider() {
        return EVcsProvider.BITBUCKET_CLOUD;
    }

    /**
     * Helper class to hold VCS connection info from either ProjectVcsConnectionBinding or VcsRepoBinding.
     */
    private record VcsInfo(VcsConnection vcsConnection, String workspace, String repoSlug) {}

    /**
     * Get VCS info from the project, using VcsRepoBinding as fallback if ProjectVcsConnectionBinding is null.
     */
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

        // Fetch PR diff and extract metadata for RAG
        List<String> changedFiles = Collections.emptyList();
        List<String> diffSnippets = Collections.emptyList();
        String prTitle = null;
        String prDescription = null;

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

            // Fetch PR diff
            GetPullRequestDiffAction diffAction = new GetPullRequestDiffAction(client);
            String rawDiff = diffAction.getPullRequestDiff(
                    vcsInfo.workspace(),
                    vcsInfo.repoSlug(),
                    String.valueOf(request.getPullRequestId())
            );

            // Parse diff to extract changed files and code snippets
            changedFiles = DiffParser.extractChangedFiles(rawDiff);
            diffSnippets = DiffParser.extractDiffSnippets(rawDiff, 20);

            log.info("Extracted {} changed files and {} code snippets from PR diff",
                    changedFiles.size(), diffSnippets.size());

        } catch (IOException e) {
            log.warn("Failed to fetch/parse PR metadata/diff for RAG context: {}", e.getMessage());
            // Continue without metadata - RAG will use fallback
        }

        var builder = AiPullRequestAnalysisRequest.builder()
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
                .withProjectMetadata(project.getWorkspace().getName(), project.getNamespace())
                .withTargetBranchName(request.targetBranchName)
                .withVcsProvider("bitbucket_cloud");
        
        // Add VCS credentials based on connection type
        addVcsCredentials(builder, vcsConnection);
        
        return builder.build();
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

        var builder = AiBranchAnalysisRequest.builder()
                .withProjectId(project.getId())
                .withPullRequestId(null)
                .withProjectAiConnection(aiConnection)
                .withProjectVcsConnectionBindingInfo(vcsInfo.workspace(), vcsInfo.repoSlug())
                .withProjectAiConnectionTokenDecrypted(tokenEncryptionService.decrypt(projectAiConnection.getApiKeyEncrypted()))
                .withUseLocalMcp(true)
                .withPreviousAnalysisData(previousAnalysis)
                .withMaxAllowedTokens(aiConnection.getTokenLimitation())
                .withAnalysisType(request.getAnalysisType())
                .withBranch(request.getTargetBranchName())
                .withCommitHash(request.getCommitHash())
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
    private void addVcsCredentials(AiPullRequestAnalysisRequest.Builder builder, VcsConnection connection) 
            throws GeneralSecurityException {
        if (connection.getConnectionType() == EVcsConnectionType.APP && connection.getAccessToken() != null) {
            String accessToken = tokenEncryptionService.decrypt(connection.getAccessToken());
            builder.withAccessToken(accessToken);
        } else if (connection.getConfiguration() instanceof BitbucketCloudConfig config) {
            builder.withProjectVcsConnectionCredentials(
                    tokenEncryptionService.decrypt(config.oAuthKey()),
                    tokenEncryptionService.decrypt(config.oAuthToken())
            );
        } else {
            log.warn("Unknown connection type for VCS credentials: {}", connection.getConnectionType());
        }
    }
    
    private void addVcsCredentials(AiBranchAnalysisRequest.Builder builder, VcsConnection connection) 
            throws GeneralSecurityException {
        if (connection.getConnectionType() == EVcsConnectionType.APP && connection.getAccessToken() != null) {
            String accessToken = tokenEncryptionService.decrypt(connection.getAccessToken());
            builder.withAccessToken(accessToken);
        } else if (connection.getConfiguration() instanceof BitbucketCloudConfig config) {
            builder.withProjectVcsConnectionCredentials(
                    tokenEncryptionService.decrypt(config.oAuthKey()),
                    tokenEncryptionService.decrypt(config.oAuthToken())
            );
        } else {
            log.warn("Unknown connection type for VCS credentials: {}", connection.getConnectionType());
        }
    }
}
