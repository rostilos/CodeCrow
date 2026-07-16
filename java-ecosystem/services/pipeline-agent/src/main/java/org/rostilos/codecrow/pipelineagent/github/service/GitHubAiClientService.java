package org.rostilos.codecrow.pipelineagent.github.service;

import java.io.IOException;

import com.fasterxml.jackson.databind.JsonNode;
import okhttp3.OkHttpClient;
import org.rostilos.codecrow.analysisengine.service.pr.PrFileEnrichmentService;
import org.rostilos.codecrow.analysisengine.service.pr.PullRequestDiffPreparationService;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.pipelineagent.generic.service.AbstractVcsAiClientService;
import org.rostilos.codecrow.pipelineagent.generic.service.TaskContextEnrichmentService;
import org.rostilos.codecrow.pipelineagent.generic.service.TaskHistoryContextService;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.rostilos.codecrow.vcsclient.github.actions.GetCommitComparisonAction;
import org.rostilos.codecrow.vcsclient.github.actions.GetCommitRangeDiffAction;
import org.rostilos.codecrow.vcsclient.github.actions.GetPullRequestAction;
import org.rostilos.codecrow.vcsclient.github.actions.GetPullRequestDiffAction;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

@Service
public class GitHubAiClientService extends AbstractVcsAiClientService {
    public GitHubAiClientService(
            TokenEncryptionService tokenEncryptionService,
            VcsClientProvider vcsClientProvider,
            @Autowired(required = false) PrFileEnrichmentService enrichmentService,
            @Autowired(required = false) TaskContextEnrichmentService taskContextEnrichmentService,
            @Autowired(required = false) TaskHistoryContextService taskHistoryContextService,
            PullRequestDiffPreparationService diffPreparationService) {
        super(tokenEncryptionService, vcsClientProvider, enrichmentService,
                taskContextEnrichmentService, taskHistoryContextService, diffPreparationService);
    }

    @Override
    public EVcsProvider getProvider() {
        return EVcsProvider.GITHUB;
    }

    @Override
    protected PullRequestMetadata fetchPullRequestMetadata(
            OkHttpClient client,
            RepositoryInfo repository,
            long pullRequestId) throws IOException {
        JsonNode metadata = new GetPullRequestAction(client).getPullRequest(
                repository.workspace(), repository.repoSlug(), Math.toIntExact(pullRequestId));
        String baseSha = metadata.path("base").path("sha").asText(null);
        String headSha = metadata.path("head").path("sha").asText(null);
        JsonNode comparison = new GetCommitComparisonAction(client).getCommitComparison(
                repository.workspace(), repository.repoSlug(), baseSha, headSha);

        return pullRequestMetadata(
                metadata.path("title").asText(null),
                metadata.path("body").asText(null),
                baseSha,
                headSha,
                comparison.path("merge_base_commit").path("sha").asText(null));
    }

    @Override
    protected PullRequestData fetchPullRequest(
            OkHttpClient client,
            RepositoryInfo repository,
            long pullRequestId) throws IOException {
        JsonNode metadata = new GetPullRequestAction(client).getPullRequest(
                repository.workspace(), repository.repoSlug(), Math.toIntExact(pullRequestId));
        String diff = new GetPullRequestDiffAction(client).getPullRequestDiff(
                repository.workspace(), repository.repoSlug(), Math.toIntExact(pullRequestId));
        return pullRequestData(
                metadata.path("title").asText(null),
                metadata.path("body").asText(null),
                diff,
                metadata.path("base").path("sha").asText(null));
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
}
