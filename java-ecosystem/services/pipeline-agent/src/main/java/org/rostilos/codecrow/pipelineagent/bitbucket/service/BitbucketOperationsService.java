package org.rostilos.codecrow.pipelineagent.bitbucket.service;

import okhttp3.OkHttpClient;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsOperationsService;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions.CheckFileExistsInBranchAction;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions.GetCommitDiffAction;
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
    public boolean checkFileExistsInBranch(OkHttpClient client, String workspace, String repoSlug, String branchName, String filePath) throws IOException {
        CheckFileExistsInBranchAction action = new CheckFileExistsInBranchAction(client);
        return action.fileExists(workspace, repoSlug, branchName, filePath);
    }
}
