package org.rostilos.codecrow.pipelineagent.github.service;

import okhttp3.OkHttpClient;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsOperationsService;
import org.rostilos.codecrow.vcsclient.github.actions.CheckFileExistsInBranchAction;
import org.rostilos.codecrow.vcsclient.github.actions.GetCommitDiffAction;
import org.rostilos.codecrow.vcsclient.github.actions.GetPullRequestDiffAction;
import org.springframework.stereotype.Service;

import java.io.IOException;

/**
 * GitHub implementation of VcsOperationsService.
 * Delegates to GitHub-specific action classes for API calls.
 */
@Service
public class GitHubOperationsService implements VcsOperationsService {

    @Override
    public EVcsProvider getProvider() {
        return EVcsProvider.GITHUB;
    }

    @Override
    public String getCommitDiff(OkHttpClient client, String owner, String repoSlug, String commitHash) throws IOException {
        GetCommitDiffAction action = new GetCommitDiffAction(client);
        return action.getCommitDiff(owner, repoSlug, commitHash);
    }

    @Override
    public String getPullRequestDiff(OkHttpClient client, String owner, String repoSlug, String prNumber) throws IOException {
        GetPullRequestDiffAction action = new GetPullRequestDiffAction(client);
        return action.getPullRequestDiff(owner, repoSlug, Integer.parseInt(prNumber));
    }

    @Override
    public boolean checkFileExistsInBranch(OkHttpClient client, String owner, String repoSlug, String branchName, String filePath) throws IOException {
        CheckFileExistsInBranchAction action = new CheckFileExistsInBranchAction(client);
        return action.fileExists(owner, repoSlug, branchName, filePath);
    }
}
