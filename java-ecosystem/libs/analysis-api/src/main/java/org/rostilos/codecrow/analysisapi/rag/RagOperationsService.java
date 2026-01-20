package org.rostilos.codecrow.analysisapi.rag;

import org.rostilos.codecrow.core.model.project.Project;

import java.util.function.Consumer;
import java.util.Map;

/**
 * Interface for RAG (Retrieval-Augmented Generation) operations.
 * 
 * This interface defines the contract for RAG operations, allowing modules to depend on
 * the interface without requiring the full RAG implementation. This enables:
 * - analysis-engine to use RAG operations without directly depending on rag-engine
 * - Optional RAG support (implementations can be conditionally loaded)
 * - Easy testing with mock implementations
 * 
 * Implementations are provided by the rag-engine module.
 */
public interface RagOperationsService {

    /**
     * Check if RAG is enabled for the given project.
     * 
     * @param project The project to check
     * @return true if RAG is enabled, false otherwise
     */
    boolean isRagEnabled(Project project);

    /**
     * Check if RAG index is in a ready state for the given project.
     * 
     * @param project The project to check
     * @return true if RAG index is ready, false otherwise
     */
    boolean isRagIndexReady(Project project);

    /**
     * Trigger an incremental RAG update for the given project after a branch merge or commit.
     * 
     * @param project The project to update
     * @param branchName The branch name that was updated
     * @param commitHash The commit hash of the update
     * @param rawDiff The raw diff from the VCS (used to determine which files changed)
     * @param eventConsumer Consumer to receive status updates during processing
     */
    void triggerIncrementalUpdate(
            Project project, 
            String branchName, 
            String commitHash,
            String rawDiff,
            Consumer<Map<String, Object>> eventConsumer
    );
    
    // ==========================================================================
    // MULTI-BRANCH INDEX OPERATIONS
    // ==========================================================================
    
    /**
     * Check if multi-branch indexing is enabled for the given project.
     * 
     * @param project The project to check
     * @return true if multi-branch indexing is enabled
     */
    default boolean isMultiBranchEnabled(Project project) {
        var config = project.getConfiguration();
        if (config == null || config.ragConfig() == null) {
            return false;
        }
        return config.ragConfig().isMultiBranchEnabled();
    }
    
    /**
     * Check if a branch should have indexed context based on project configuration.
     * Branch indexes are created for branches that match branchPushPatterns in BranchAnalysisConfig.
     * 
     * @param project The project to check
     * @param branchName The branch name to evaluate
     * @return true if branch should have indexed context
     */
    default boolean shouldHaveBranchIndex(Project project, String branchName) {
        var config = project.getConfiguration();
        if (config == null || config.ragConfig() == null) {
            return false;
        }
        // Get branch push patterns from branch analysis config
        var branchPushPatterns = config.branchAnalysis() != null 
            ? config.branchAnalysis().branchPushPatterns() 
            : null;
        return config.ragConfig().shouldHaveBranchIndex(branchName, branchPushPatterns);
    }
    
    /**
     * Get the base branch for RAG indexing.
     * 
     * @param project The project
     * @return The base branch name (e.g., "master" or "main")
     */
    default String getBaseBranch(Project project) {
        var config = project.getConfiguration();
        if (config != null && config.ragConfig() != null && config.ragConfig().branch() != null) {
            return config.ragConfig().branch();
        }
        // Use main branch from project config (single source of truth)
        if (config != null) {
            return config.mainBranch();
        }
        return "main";
    }
    
    /**
     * Create or update branch index for multi-branch context.
     * With single-collection architecture, branch data is stored in shared collection
     * with branch metadata for filtering.
     * 
     * @param project The project
     * @param branchName The branch to index (e.g., "release/1.0")
     * @param baseBranch The base branch (e.g., "master")
     * @param branchCommit The commit hash of the branch
     * @param rawDiff The raw diff from VCS
     * @param eventConsumer Consumer to receive status updates
     */
    default void createOrUpdateBranchIndex(
            Project project,
            String branchName,
            String baseBranch,
            String branchCommit,
            String rawDiff,
            Consumer<Map<String, Object>> eventConsumer
    ) {
        // Default implementation does nothing - override in actual implementation
        eventConsumer.accept(Map.of(
            "type", "warning",
            "message", "Branch index operations not implemented"
        ));
    }
    
    /**
     * Update branch index by calculating diff between base branch and target branch.
     * 
     * This method always recalculates the full diff between the base branch (e.g., "master")
     * and the target branch (e.g., "release/1.0"), then indexes all changed files with
     * the target branch in their metadata.
     * 
     * Use this when a push happens to a non-main branch and you need to update
     * the RAG index to reflect the current state of that branch.
     * 
     * @param project The project
     * @param targetBranch The branch that was pushed to (e.g., "release/1.0")
     * @param eventConsumer Consumer to receive status updates
     * @return true if update succeeded, false otherwise
     */
    default boolean updateBranchIndex(
            Project project,
            String targetBranch,
            Consumer<Map<String, Object>> eventConsumer
    ) {
        eventConsumer.accept(Map.of(
            "type", "warning",
            "message", "Branch index update not implemented"
        ));
        return false;
    }
    
    /**
     * Check if a branch has indexed data.
     * 
     * @param project The project
     * @param branchName The branch to check
     * @return true if branch has indexed data
     */
    default boolean isBranchIndexReady(Project project, String branchName) {
        return false;
    }
    
    /**
     * Delete all indexed data for a branch.
     * This removes the branch's points from the collection and cleans up the database record.
     * Used when a branch is deleted or merged.
     * 
     * @param project The project
     * @param branchName The branch to delete
     * @param eventConsumer Consumer to receive status updates
     * @return true if deletion succeeded, false otherwise
     */
    default boolean deleteBranchIndex(
            Project project,
            String branchName,
            Consumer<Map<String, Object>> eventConsumer
    ) {
        eventConsumer.accept(Map.of(
            "type", "warning",
            "message", "Branch index deletion not implemented"
        ));
        return false;
    }
    
    /**
     * Cleanup stale branches from RAG index.
     * Compares indexed branches against active branches from VCS and removes orphaned data.
     * 
     * @param project The project
     * @param activeBranches Set of currently active branch names from VCS
     * @param eventConsumer Consumer to receive status updates
     * @return Map with cleanup results (deleted_branches, failed_branches, etc.)
     */
    default Map<String, Object> cleanupStaleBranches(
            Project project,
            java.util.Set<String> activeBranches,
            Consumer<Map<String, Object>> eventConsumer
    ) {
        eventConsumer.accept(Map.of(
            "type", "warning",
            "message", "Stale branch cleanup not implemented"
        ));
        return Map.of("status", "not_implemented");
    }
    
    /**
     * Decision record for multi-branch RAG usage.
     */
    record MultiBranchRagDecision(
        boolean useMultiBranch,
        String baseBranch,
        String targetBranch,
        boolean branchIndexAvailable,
        String reason
    ) {}
    
    /**
     * Determine if multi-branch RAG should be used for a PR.
     * 
     * @param project The project
     * @param targetBranch The PR target branch
     * @return Decision about whether to use multi-branch RAG
     */
    default MultiBranchRagDecision shouldUseMultiBranchRag(Project project, String targetBranch) {
        if (!isRagEnabled(project)) {
            return new MultiBranchRagDecision(false, null, targetBranch, false, "rag_disabled");
        }
        
        String baseBranch = getBaseBranch(project);
        
        // If target is the base branch, no need for multi-branch
        if (baseBranch.equals(targetBranch)) {
            return new MultiBranchRagDecision(false, baseBranch, targetBranch, false, "target_is_base");
        }
        
        // Check if multi-branch is enabled and available
        if (!isMultiBranchEnabled(project)) {
            return new MultiBranchRagDecision(false, baseBranch, targetBranch, false, "multi_branch_disabled");
        }
        
        boolean branchReady = isBranchIndexReady(project, targetBranch);
        if (branchReady) {
            return new MultiBranchRagDecision(true, baseBranch, targetBranch, true, "branch_index_available");
        } else {
            return new MultiBranchRagDecision(false, baseBranch, targetBranch, false, "branch_index_not_ready");
        }
    }
    
    /**
     * Ensure branch index exists for a PR target branch if needed.
     * This is called during PR analysis to create branch index on-demand
     * when the target branch should have one but doesn't exist yet.
     * 
     * @param project The project
     * @param targetBranch The PR target branch
     * @param eventConsumer Consumer to receive status updates
     * @return true if branch index is ready (either existed or was created), false otherwise
     */
    default boolean ensureBranchIndexForPrTarget(
            Project project,
            String targetBranch,
            Consumer<Map<String, Object>> eventConsumer
    ) {
        // Default implementation - override in actual implementation
        return false;
    }
    
    /**
     * Ensure RAG index is up-to-date for PR analysis.
     * 
     * For PRs targeting the main branch:
     * - Check if the RAG index commit matches the current target branch HEAD
     * - If not, fetch the diff and perform incremental update
     * 
     * For PRs targeting other branches with multi-branch enabled:
     * - Check if the branch index commit matches the current target branch HEAD
     * - If not, update the branch index with the diff
     * 
     * @param project The project
     * @param targetBranch The PR target branch
     * @param eventConsumer Consumer to receive status updates
     * @return true if index is ready for analysis, false otherwise
     */
    default boolean ensureRagIndexUpToDate(
            Project project,
            String targetBranch,
            Consumer<Map<String, Object>> eventConsumer
    ) {
        // Default implementation - override in actual implementation
        return false;
    }
}
