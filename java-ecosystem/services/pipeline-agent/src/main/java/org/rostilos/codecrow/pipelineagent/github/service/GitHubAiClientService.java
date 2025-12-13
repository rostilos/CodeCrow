package org.rostilos.codecrow.pipelineagent.github.service;

import com.fasterxml.jackson.databind.JsonNode;
import okhttp3.OkHttpClient;
import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.ProjectVcsConnectionBinding;
import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
import org.rostilos.codecrow.core.model.vcs.config.github.GitHubConfig;
import org.rostilos.codecrow.pipelineagent.generic.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.pipelineagent.generic.dto.request.ai.AiAnalysisRequestImpl;
import org.rostilos.codecrow.pipelineagent.generic.dto.request.processor.AnalysisProcessRequest;
import org.rostilos.codecrow.pipelineagent.generic.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.pipelineagent.generic.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.pipelineagent.generic.service.vcs.VcsAiClientService;
import org.rostilos.codecrow.pipelineagent.generic.util.DiffParser;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.rostilos.codecrow.vcsclient.github.actions.GetPullRequestAction;
import org.rostilos.codecrow.vcsclient.github.actions.GetPullRequestDiffAction;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.security.GeneralSecurityException;
import java.util.Collections;
import java.util.List;
import java.util.Optional;

@Service
public class GitHubAiClientService implements VcsAiClientService {
    private static final Logger log = LoggerFactory.getLogger(GitHubAiClientService.class);

    private final TokenEncryptionService tokenEncryptionService;
    private final VcsClientProvider vcsClientProvider;

    public GitHubAiClientService(
            TokenEncryptionService tokenEncryptionService,
            VcsClientProvider vcsClientProvider
    ) {
        this.tokenEncryptionService = tokenEncryptionService;
        this.vcsClientProvider = vcsClientProvider;
    }

    @Override
    public EVcsProvider getProvider() {
        return EVcsProvider.GITHUB;
    }

    private record VcsInfo(VcsConnection vcsConnection, String owner, String repoSlug) {}

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
                return buildPrAnalysisRequest(project, (PrProcessRequest) request, previousAnalysis);
        }
    }

    private AiAnalysisRequest buildPrAnalysisRequest(
            Project project,
            PrProcessRequest request,
            Optional<CodeAnalysis> previousAnalysis
    ) throws GeneralSecurityException {
        VcsInfo vcsInfo = getVcsInfo(project);
        VcsConnection vcsConnection = vcsInfo.vcsConnection();
        AIConnection aiConnection = project.getAiBinding().getAiConnection();

        List<String> changedFiles = Collections.emptyList();
        List<String> diffSnippets = Collections.emptyList();
        String prTitle = null;
        String prDescription = null;

        try {
            OkHttpClient client = vcsClientProvider.getHttpClient(vcsConnection);

            GetPullRequestAction prAction = new GetPullRequestAction(client);
            JsonNode prData = prAction.getPullRequest(
                    vcsInfo.owner(),
                    vcsInfo.repoSlug(),
                    request.getPullRequestId().intValue()
            );

            prTitle = prData.has("title") ? prData.get("title").asText() : null;
            prDescription = prData.has("body") ? prData.get("body").asText() : null;

            log.info("Fetched PR metadata: title='{}', description length={}",
                    prTitle, prDescription != null ? prDescription.length() : 0);

            GetPullRequestDiffAction diffAction = new GetPullRequestDiffAction(client);
            String rawDiff = diffAction.getPullRequestDiff(
                    vcsInfo.owner(),
                    vcsInfo.repoSlug(),
                    request.getPullRequestId().intValue()
            );

            changedFiles = DiffParser.extractChangedFiles(rawDiff);
            diffSnippets = DiffParser.extractDiffSnippets(rawDiff, 20);

            log.info("Extracted {} changed files and {} code snippets from PR diff",
                    changedFiles.size(), diffSnippets.size());

        } catch (IOException e) {
            log.warn("Failed to fetch/parse PR metadata/diff for RAG context: {}", e.getMessage());
        }

        var builder = AiAnalysisRequestImpl.builder()
                .withProjectId(project.getId())
                .withPullRequestId(request.getPullRequestId())
                .withProjectAiConnection(aiConnection)
                .withProjectVcsConnectionBindingInfo(vcsInfo.owner(), vcsInfo.repoSlug())
                .withProjectAiConnectionTokenDecrypted(tokenEncryptionService.decrypt(aiConnection.getApiKeyEncrypted()))
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
                .withVcsProvider("github");
        
        addVcsCredentials(builder, vcsConnection);
        
        return builder.build();
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
                .withProjectVcsConnectionBindingInfo(vcsInfo.owner(), vcsInfo.repoSlug())
                .withProjectAiConnectionTokenDecrypted(tokenEncryptionService.decrypt(aiConnection.getApiKeyEncrypted()))
                .withUseLocalMcp(true)
                .withPreviousAnalysisData(previousAnalysis)
                .withMaxAllowedTokens(aiConnection.getTokenLimitation())
                .withAnalysisType(request.getAnalysisType())
                .withProjectMetadata(project.getWorkspace().getName(), project.getNamespace());
        
        addVcsCredentials(builder, vcsConnection);
        
        return builder.build();
    }
    
    private void addVcsCredentials(AiAnalysisRequestImpl.Builder builder, VcsConnection connection) 
            throws GeneralSecurityException {
        if (connection.getConnectionType() == EVcsConnectionType.APP && connection.getAccessToken() != null) {
            String accessToken = tokenEncryptionService.decrypt(connection.getAccessToken());
            builder.withAccessToken(accessToken);
        } else if (connection.getConnectionType() == EVcsConnectionType.PERSONAL_TOKEN && 
                   connection.getConfiguration() instanceof GitHubConfig config) {
            builder.withAccessToken(config.accessToken());
        } else {
            log.warn("Unknown connection type for VCS credentials: {}", connection.getConnectionType());
        }
    }
}
