package org.rostilos.codecrow.pipelineagent.bitbucket.service;

import okhttp3.OkHttpClient;
import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.ProjectVcsConnectionBinding;
import org.rostilos.codecrow.core.model.vcs.config.cloud.BitbucketCloudConfig;
import org.rostilos.codecrow.pipelineagent.bitbucket.dto.request.ai.AiBranchAnalysisRequest;
import org.rostilos.codecrow.pipelineagent.bitbucket.dto.request.ai.AiPullRequestAnalysisRequest;
import org.rostilos.codecrow.pipelineagent.generic.dto.request.pipelineagent.AnalysisRequest;
import org.rostilos.codecrow.pipelineagent.bitbucket.dto.request.pipelineagent.BranchProcessRequest;
import org.rostilos.codecrow.pipelineagent.bitbucket.dto.request.pipelineagent.PrProcessRequest;
import org.rostilos.codecrow.pipelineagent.generic.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.pipelineagent.bitbucket.util.DiffParser;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions.GetPullRequestAction;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions.GetPullRequestDiffAction;
import org.rostilos.codecrow.vcsclient.bitbucket.service.VcsConnectionService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.security.GeneralSecurityException;
import java.util.Collections;
import java.util.List;
import java.util.Optional;

@Service
public class BitbucketAiClientService {
    private static final Logger log = LoggerFactory.getLogger(BitbucketAiClientService.class);

    private final TokenEncryptionService tokenEncryptionService;
    private final VcsConnectionService vcsConnectionService;

    public BitbucketAiClientService(
            TokenEncryptionService tokenEncryptionService,
            VcsConnectionService vcsConnectionService
    ) {
        this.tokenEncryptionService = tokenEncryptionService;
        this.vcsConnectionService = vcsConnectionService;
    }

    public AiAnalysisRequest buildAiAnalysisRequest(
            Project project,
            AnalysisRequest request,
            Optional<CodeAnalysis> previousAnalysis
    ) throws GeneralSecurityException {
        switch (request.getAnalysisType()) {
            case BRANCH_ANALYSIS:
                return buildBranchAnalysisRequest(project, (BranchProcessRequest) request, previousAnalysis);
            default:
                return buildPrAnalysisRequest(project, (PrProcessRequest) request, previousAnalysis);
        }
    }

    public AiAnalysisRequest buildPrAnalysisRequest(
            Project project,
            PrProcessRequest request,
            Optional<CodeAnalysis> previousAnalysis
    ) throws GeneralSecurityException {
        ProjectVcsConnectionBinding vcsBinding = project.getVcsBinding();
        AIConnection aiConnection = project.getAiBinding().getAiConnection();
        AIConnection projectAiConnection = project.getAiBinding().getAiConnection();
        BitbucketCloudConfig bitbucketCloudConfig = (BitbucketCloudConfig) vcsBinding.getVcsConnection().getConfiguration();

        // Fetch PR diff and extract metadata for RAG
        List<String> changedFiles = Collections.emptyList();
        List<String> diffSnippets = Collections.emptyList();
        String prTitle = null;
        String prDescription = null;

        try {
            OkHttpClient client = vcsConnectionService.getBitbucketAuthorizedClient(
                    project.getWorkspace().getId(),
                    vcsBinding.getVcsConnection().getId()
            );

            // Fetch PR metadata
            GetPullRequestAction prAction = new GetPullRequestAction(client);
            GetPullRequestAction.PullRequestMetadata prMetadata = prAction.getPullRequest(
                    vcsBinding.getWorkspace(),
                    vcsBinding.getRepoSlug(),
                    String.valueOf(request.getPullRequestId())
            );

            prTitle = prMetadata.getTitle();
            prDescription = prMetadata.getDescription();

            log.info("Fetched PR metadata: title='{}', description length={}",
                    prTitle, prDescription != null ? prDescription.length() : 0);

            // Fetch PR diff
            GetPullRequestDiffAction diffAction = new GetPullRequestDiffAction(client);
            String rawDiff = diffAction.getPullRequestDiff(
                    vcsBinding.getWorkspace(),
                    vcsBinding.getRepoSlug(),
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

        return AiPullRequestAnalysisRequest.builder()
                .withProjectId(project.getId())
                .withPullRequestId(request.getPullRequestId())
                .withProjectAiConnection(aiConnection)
                .withProjectVcsConnectionBinding(vcsBinding)
                .withProjectAiConnectionTokenDecrypted(tokenEncryptionService.decrypt(projectAiConnection.getApiKeyEncrypted()))
                .withProjectVcsConnectionCredentials(
                        tokenEncryptionService.decrypt(bitbucketCloudConfig.oAuthKey()),
                        tokenEncryptionService.decrypt(bitbucketCloudConfig.oAuthToken())
                )
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
                .build();
    }

    public AiAnalysisRequest buildBranchAnalysisRequest(
            Project project,
            BranchProcessRequest request,
            Optional<CodeAnalysis> previousAnalysis
    ) throws GeneralSecurityException {
        ProjectVcsConnectionBinding vcsBinding = project.getVcsBinding();
        AIConnection aiConnection = project.getAiBinding().getAiConnection();
        AIConnection projectAiConnection = project.getAiBinding().getAiConnection();
        BitbucketCloudConfig bitbucketCloudConfig = (BitbucketCloudConfig) vcsBinding.getVcsConnection().getConfiguration();


        return AiBranchAnalysisRequest.builder()
                .withProjectId(project.getId())
                .withPullRequestId(null)
                .withProjectAiConnection(aiConnection)
                .withProjectVcsConnectionBinding(vcsBinding)
                .withProjectAiConnectionTokenDecrypted(tokenEncryptionService.decrypt(projectAiConnection.getApiKeyEncrypted()))
                .withProjectVcsConnectionCredentials(
                        tokenEncryptionService.decrypt(bitbucketCloudConfig.oAuthKey()),
                        tokenEncryptionService.decrypt(bitbucketCloudConfig.oAuthToken())
                )
                .withUseLocalMcp(true)
                .withPreviousAnalysisData(previousAnalysis)
                .withMaxAllowedTokens(aiConnection.getTokenLimitation())
                .withAnalysisType(request.getAnalysisType())
                .withBranch(request.getTargetBranchName())
                .withCommitHash(request.getCommitHash())
                .withProjectMetadata(project.getWorkspace().getName(), project.getNamespace())
                .build();
    }
}
