package org.rostilos.codecrow.pipelineagent.bitbucket.service;

import okhttp3.OkHttpClient;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsOperationsService;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions.CheckFileExistsInBranchAction;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions.GetCommitDiffAction;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions.GetCommitRangeDiffAction;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions.GetPullRequestDiffAction;
import org.springframework.stereotype.Service;

import java.io.IOException;

/**
 * Bitbucket implementation of VcsOperationsService.
 * Delegates to Bitbucket-specific action classes for API calls.
 */
@Service
public class BitbucketOperationsService implements VcsOperationsService {

    @Override
    public EVcsProvider getProvider() {
        return EVcsProvider.BITBUCKET_CLOUD;
    }

    @Override
    public String getCommitDiff(OkHttpClient client, String workspace, String repoSlug, String commitHash) throws IOException {
        GetCommitDiffAction action = new GetCommitDiffAction(client);
        return action.getCommitDiff(workspace, repoSlug, commitHash);
    }

    @Override
    public String getPullRequestDiff(OkHttpClient client, String workspace, String repoSlug, String prNumber) throws IOException {
        GetPullRequestDiffAction action = new GetPullRequestDiffAction(client);
        return action.getPullRequestDiff(workspace, repoSlug, prNumber);
    }

    @Override
    public String getCommitRangeDiff(OkHttpClient client, String workspace, String repoSlug, String baseCommitHash, String headCommitHash) throws IOException {
        GetCommitRangeDiffAction action = new GetCommitRangeDiffAction(client);
        return action.getCommitRangeDiff(workspace, repoSlug, baseCommitHash, headCommitHash);
    }

    @Override
    public boolean checkFileExistsInBranch(OkHttpClient client, String workspace, String repoSlug, String branchName, String filePath) throws IOException {
        CheckFileExistsInBranchAction action = new CheckFileExistsInBranchAction(client);
        return action.fileExists(workspace, repoSlug, branchName, filePath);
    }
}
