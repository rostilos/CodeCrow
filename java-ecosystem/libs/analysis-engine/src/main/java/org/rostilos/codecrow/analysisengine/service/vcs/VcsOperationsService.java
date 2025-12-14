package org.rostilos.codecrow.analysisengine.service.vcs;

import okhttp3.OkHttpClient;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;

import java.io.IOException;

/**
 * Generic interface for VCS operations that vary by provider.
 * Implementations handle provider-specific API calls for operations like
 * fetching diffs and checking file existence.
 */
public interface VcsOperationsService {

    /**
     * @return the VCS provider this service handles
     */
    EVcsProvider getProvider();

    /**
     * Fetches the raw diff for a commit.
     *
     * @param client authorized HTTP client
     * @param workspace workspace or team/organization slug
     * @param repoSlug repository slug
     * @param commitHash commit hash
     * @return raw unified diff as returned by VCS API
     * @throws IOException on network / parsing errors
     */
    String getCommitDiff(OkHttpClient client, String workspace, String repoSlug, String commitHash) throws IOException;

    /**
     * Checks if a file exists in the specified branch.
     *
     * @param client authorized HTTP client
     * @param workspace workspace or team/organization slug
     * @param repoSlug repository slug
     * @param branchName branch name (or commit hash)
     * @param filePath file path relative to repository root
     * @return true if file exists in the branch, false otherwise
     * @throws IOException on network errors
     */
    boolean checkFileExistsInBranch(OkHttpClient client, String workspace, String repoSlug, String branchName, String filePath) throws IOException;
}
