package org.rostilos.codecrow.ragengine.service;

import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.ragengine.client.RagPipelineClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.util.Map;

/**
 * Service for managing delta indexes in the RAG pipeline.
 * Delta indexes store only the differences between a branch and the base branch,
 * enabling efficient hybrid RAG queries.
 */
@Service
public class DeltaIndexService {

    private static final Logger log = LoggerFactory.getLogger(DeltaIndexService.class);

    private final RagPipelineClient ragPipelineClient;

    public DeltaIndexService(RagPipelineClient ragPipelineClient) {
        this.ragPipelineClient = ragPipelineClient;
    }

    /**
     * Create or update a delta index for a branch.
     *
     * @param project The project
     * @param vcsConnection The VCS connection
     * @param workspaceSlug The workspace slug
     * @param repoSlug The repository slug
     * @param deltaBranch The branch to create delta for
     * @param baseBranch The base branch
     * @param deltaCommit The commit hash of the delta branch
     * @param rawDiff The raw diff from VCS
     * @return Map containing creation results (chunkCount, fileCount, collectionName, baseCommitHash)
     */
    public Map<String, Object> createOrUpdateDeltaIndex(
            Project project,
            VcsConnection vcsConnection,
            String workspaceSlug,
            String repoSlug,
            String deltaBranch,
            String baseBranch,
            String deltaCommit,
            String rawDiff
    ) {
        log.info("Creating delta index for project={}, deltaBranch={}, baseBranch={}",
                project.getId(), deltaBranch, baseBranch);

        try {
            Map<String, Object> response = ragPipelineClient.createDeltaIndex(
                    workspaceSlug,
                    repoSlug,
                    deltaBranch,
                    baseBranch,
                    deltaCommit,
                    rawDiff,
                    vcsConnection.getProviderType().name().toLowerCase()
            );

            String collectionName = (String) response.get("collection_name");
            if (collectionName == null) {
                collectionName = "";
            }
            int fileCount = response.get("file_count") != null ? 
                    ((Number) response.get("file_count")).intValue() : 0;
            int chunkCount = response.get("chunk_count") != null ? 
                    ((Number) response.get("chunk_count")).intValue() : 0;
            String baseCommitHash = (String) response.get("base_commit_hash");
            if (baseCommitHash == null) {
                baseCommitHash = "";
            }

            log.info("Delta index created: collection={}, files={}, chunks={}",
                    collectionName, fileCount, chunkCount);

            return Map.of(
                    "collectionName", collectionName,
                    "fileCount", fileCount,
                    "chunkCount", chunkCount,
                    "baseCommitHash", baseCommitHash
            );
        } catch (IOException e) {
            throw new RuntimeException("Failed to create delta index: " + e.getMessage(), e);
        }
    }

    /**
     * Update an existing delta index.
     *
     * @param project The project
     * @param vcsConnection The VCS connection
     * @param workspaceSlug The workspace slug
     * @param repoSlug The repository slug
     * @param deltaBranch The branch to update
     * @param newCommit The new commit hash
     * @param rawDiff The raw diff
     * @return Map containing update results
     */
    public Map<String, Object> updateDeltaIndex(
            Project project,
            VcsConnection vcsConnection,
            String workspaceSlug,
            String repoSlug,
            String deltaBranch,
            String newCommit,
            String rawDiff
    ) {
        log.info("Updating delta index for project={}, deltaBranch={}, newCommit={}",
                project.getId(), deltaBranch, newCommit);

        try {
            Map<String, Object> response = ragPipelineClient.updateDeltaIndex(
                    workspaceSlug,
                    repoSlug,
                    deltaBranch,
                    newCommit,
                    rawDiff,
                    vcsConnection.getProviderType().name().toLowerCase()
            );

            String collectionName = (String) response.get("collection_name");
            if (collectionName == null) {
                collectionName = "";
            }
            int fileCount = response.get("file_count") != null ? 
                    ((Number) response.get("file_count")).intValue() : 0;
            int chunkCount = response.get("chunk_count") != null ? 
                    ((Number) response.get("chunk_count")).intValue() : 0;

            return Map.of(
                    "collectionName", collectionName,
                    "fileCount", fileCount,
                    "chunkCount", chunkCount
            );
        } catch (IOException e) {
            throw new RuntimeException("Failed to update delta index: " + e.getMessage(), e);
        }
    }

    /**
     * Delete a delta index.
     *
     * @param workspaceSlug The workspace slug
     * @param repoSlug The repository slug
     * @param deltaBranch The branch to delete
     */
    public void deleteDeltaIndex(String workspaceSlug, String repoSlug, String deltaBranch) {
        log.info("Deleting delta index for workspace={}, repo={}, branch={}",
                workspaceSlug, repoSlug, deltaBranch);

        try {
            ragPipelineClient.deleteDeltaIndex(workspaceSlug, repoSlug, deltaBranch);
        } catch (IOException e) {
            throw new RuntimeException("Failed to delete delta index: " + e.getMessage(), e);
        }
    }

    /**
     * Check if a delta index exists and is ready.
     *
     * @param workspaceSlug The workspace slug
     * @param repoSlug The repository slug
     * @param deltaBranch The branch to check
     * @return true if delta index exists
     */
    public boolean deltaIndexExists(String workspaceSlug, String repoSlug, String deltaBranch) {
        return ragPipelineClient.deltaIndexExists(workspaceSlug, repoSlug, deltaBranch);
    }
}
