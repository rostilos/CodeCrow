package org.rostilos.codecrow.pipelineagent.gitlab.service;

import java.io.IOException;

import com.fasterxml.jackson.databind.JsonNode;
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
import org.rostilos.codecrow.vcsclient.gitlab.actions.GetCommitRangeDiffAction;
import org.rostilos.codecrow.vcsclient.gitlab.actions.GetMergeRequestAction;
import org.rostilos.codecrow.vcsclient.gitlab.actions.GetMergeRequestDiffAction;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

@Service
public class GitLabAiClientService extends AbstractVcsAiClientService {
    public GitLabAiClientService(
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
        return EVcsProvider.GITLAB;
    }

    @Override
    protected PullRequestData fetchPullRequest(
            OkHttpClient client,
            RepositoryInfo repository,
            long pullRequestId) throws IOException {
        JsonNode metadata = new GetMergeRequestAction(client).getMergeRequest(
                repository.workspace(), repository.repoSlug(), Math.toIntExact(pullRequestId));
        String baseCommit = metadata.path("diff_refs").path("base_sha").asText(null);
        if (baseCommit == null || baseCommit.isBlank()) {
            baseCommit = metadata.path("diff_refs").path("start_sha").asText(null);
        }
        String headCommit = metadata.path("diff_refs").path("head_sha").asText(null);
        if (headCommit == null || headCommit.isBlank()) {
            headCommit = metadata.path("sha").asText(null);
        }
        return pullRequestData(
                metadata.path("title").asText(null),
                metadata.path("description").asText(null),
                metadata.path("source_branch").asText(null),
                metadata.path("target_branch").asText(null),
                baseCommit,
                headCommit);
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
        return new GetMergeRequestDiffAction(client).getMergeRequestDiff(
                repository.workspace(), repository.repoSlug(), Math.toIntExact(pullRequestId));
    }
}
