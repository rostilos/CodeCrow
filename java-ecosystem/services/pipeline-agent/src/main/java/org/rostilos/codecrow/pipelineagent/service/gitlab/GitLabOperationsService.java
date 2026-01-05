package org.rostilos.codecrow.pipelineagent.service.gitlab;

import okhttp3.OkHttpClient;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsOperationsService;
import org.rostilos.codecrow.vcsclient.gitlab.actions.CheckFileExistsInBranchAction;
import org.rostilos.codecrow.vcsclient.gitlab.actions.GetCommitDiffAction;
import org.rostilos.codecrow.vcsclient.gitlab.actions.GetCommitRangeDiffAction;
import org.rostilos.codecrow.vcsclient.gitlab.actions.GetMergeRequestDiffAction;
import org.springframework.stereotype.Service;

import java.io.IOException;

/**
 * GitLab implementation of VcsOperationsService.
 * Delegates to GitLab-specific action classes for API calls.
 */
@Service
public class GitLabOperationsService implements VcsOperationsService {

    @Override
    public EVcsProvider getProvider() {
        return EVcsProvider.GITLAB;
    }

    @Override
    public String getCommitDiff(OkHttpClient client, String namespace, String project, String commitHash) throws IOException {
        GetCommitDiffAction action = new GetCommitDiffAction(client);
        return action.getCommitDiff(namespace, project, commitHash);
    }

    @Override
    public String getPullRequestDiff(OkHttpClient client, String namespace, String project, String mergeRequestIid) throws IOException {
        GetMergeRequestDiffAction action = new GetMergeRequestDiffAction(client);
        return action.getMergeRequestDiff(namespace, project, Integer.parseInt(mergeRequestIid));
    }

    @Override
    public String getCommitRangeDiff(OkHttpClient client, String namespace, String project, String baseCommitHash, String headCommitHash) throws IOException {
        GetCommitRangeDiffAction action = new GetCommitRangeDiffAction(client);
        return action.getCommitRangeDiff(namespace, project, baseCommitHash, headCommitHash);
    }

    @Override
    public boolean checkFileExistsInBranch(OkHttpClient client, String namespace, String project, String branchName, String filePath) throws IOException {
        CheckFileExistsInBranchAction action = new CheckFileExistsInBranchAction(client);
        return action.fileExists(namespace, project, branchName, filePath);
    }
}
