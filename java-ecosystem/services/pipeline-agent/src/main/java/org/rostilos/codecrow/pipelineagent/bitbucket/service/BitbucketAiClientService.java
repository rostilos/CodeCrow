package org.rostilos.codecrow.pipelineagent.bitbucket.service;

import java.io.IOException;

import okhttp3.OkHttpClient;
import org.rostilos.codecrow.analysisengine.service.pr.PrFileEnrichmentService;
import org.rostilos.codecrow.analysisengine.service.pr.PullRequestDiffPreparationService;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.pipelineagent.generic.service.AbstractVcsAiClientService;
import org.rostilos.codecrow.pipelineagent.generic.service.ProjectCapabilitySelectionService;
import org.rostilos.codecrow.pipelineagent.generic.service.TaskContextEnrichmentService;
import org.rostilos.codecrow.pipelineagent.generic.service.TaskHistoryContextService;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions.GetCommitAction;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions.GetCommitRangeDiffAction;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions.GetPullRequestAction;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions.GetPullRequestDiffAction;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

@Service
public class BitbucketAiClientService extends AbstractVcsAiClientService {
    public BitbucketAiClientService(
            TokenEncryptionService tokenEncryptionService,
            VcsClientProvider vcsClientProvider,
            @Autowired(required = false) PrFileEnrichmentService enrichmentService,
            @Autowired(required = false) TaskContextEnrichmentService taskContextEnrichmentService,
            @Autowired(required = false) TaskHistoryContextService taskHistoryContextService,
            ProjectCapabilitySelectionService capabilitySelectionService,
            PullRequestDiffPreparationService diffPreparationService) {
        super(tokenEncryptionService, vcsClientProvider, enrichmentService,
                taskContextEnrichmentService, taskHistoryContextService,
                capabilitySelectionService, diffPreparationService);
    }

    @Override
    public EVcsProvider getProvider() {
        return EVcsProvider.BITBUCKET_CLOUD;
    }

    @Override
    protected PullRequestData fetchPullRequest(
            OkHttpClient client,
            RepositoryInfo repository,
            long pullRequestId) throws IOException {
        GetPullRequestAction.PullRequestMetadata metadata = new GetPullRequestAction(client).getPullRequest(
                repository.workspace(), repository.repoSlug(), String.valueOf(pullRequestId));
        String destinationCommit = resolveIfAbbreviated(
                client, repository, metadata.getDestinationCommit());
        String sourceCommit = resolveIfAbbreviated(
                client, repository, metadata.getSourceCommit());
        return pullRequestData(
                metadata.getTitle(),
                metadata.getDescription(),
                metadata.getSourceRef(),
                metadata.getDestRef(),
                destinationCommit, sourceCommit);
    }

    @Override
    protected String fetchCommitRangeDiff(
            OkHttpClient client,
            RepositoryInfo repository,
            String baseCommit,
            String headCommit) throws IOException {
        return new GetCommitRangeDiffAction(client).getCommitRangeDiff(
                repository.workspace(), repository.repoSlug(), baseCommit, headCommit);
    }

    @Override
    protected String fetchPullRequestDiff(
            OkHttpClient client,
            RepositoryInfo repository,
            long pullRequestId) throws IOException {
        return new GetPullRequestDiffAction(client).getPullRequestDiff(
                repository.workspace(), repository.repoSlug(), String.valueOf(pullRequestId));
    }

    private String resolveIfAbbreviated(
            OkHttpClient client,
            RepositoryInfo repository,
            String commit) throws IOException {
        if (isFullGitObjectId(commit) || commit == null || commit.isBlank()) {
            return commit;
        }
        return new GetCommitAction(client).resolveCommitHash(
                repository.workspace(), repository.repoSlug(), commit);
    }
}
