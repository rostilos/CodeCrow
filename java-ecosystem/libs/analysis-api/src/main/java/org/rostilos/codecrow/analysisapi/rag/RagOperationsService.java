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
    // DELTA INDEX OPERATIONS (Hierarchical RAG)
    // ==========================================================================
    
    /**
     * Check if delta indexes are enabled for the given project.
     * 
     * @param project The project to check
     * @return true if delta indexes are enabled
     */
    default boolean isDeltaIndexEnabled(Project project) {
        var config = project.getConfiguration();
        if (config == null || config.ragConfig() == null) {
            return false;
        }
        return config.ragConfig().isDeltaEnabled();
    }
    
    /**
     * Check if a branch should have a delta index based on project configuration.
     * Delta indexes are created for branches that match branchPushPatterns in BranchAnalysisConfig.
     * 
     * @param project The project to check
     * @param branchName The branch name to evaluate
     * @return true if branch should have a delta index
     */
    default boolean shouldHaveDeltaIndex(Project project, String branchName) {
        var config = project.getConfiguration();
        if (config == null || config.ragConfig() == null) {
            return false;
        }
        // Get branch push patterns from branch analysis config
        var branchPushPatterns = config.branchAnalysis() != null 
            ? config.branchAnalysis().branchPushPatterns() 
            : null;
        return config.ragConfig().shouldHaveDeltaIndex(branchName, branchPushPatterns);
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
     * Create or update a delta index for a branch.
     * Delta indexes contain only the differences between a branch and the base branch,
     * enabling efficient hybrid RAG queries.
     * 
     * @param project The project
     * @param deltaBranch The branch to create delta for (e.g., "release/1.0")
     * @param baseBranch The base branch (e.g., "master")
     * @param deltaCommit The commit hash of the delta branch
     * @param rawDiff The raw diff from VCS
     * @param eventConsumer Consumer to receive status updates
     */
    default void createOrUpdateDeltaIndex(
            Project project,
            String deltaBranch,
            String baseBranch,
            String deltaCommit,
            String rawDiff,
            Consumer<Map<String, Object>> eventConsumer
    ) {
        // Default implementation does nothing - override in actual implementation
        eventConsumer.accept(Map.of(
            "type", "warning",
            "message", "Delta index operations not implemented"
        ));
    }
    
    /**
     * Check if a delta index exists and is ready for a branch.
     * 
     * @param project The project
     * @param branchName The branch to check
     * @return true if delta index is ready
     */
    default boolean isDeltaIndexReady(Project project, String branchName) {
        return false;
    }
    
    /**
     * Decision record for hybrid RAG usage.
     */
    record HybridRagDecision(
        boolean useHybrid,
        String baseBranch,
        String targetBranch,
        boolean deltaAvailable,
        String reason
    ) {}
    
    /**
     * Determine if hybrid RAG should be used for a PR.
     * 
     * @param project The project
     * @param targetBranch The PR target branch
     * @return Decision about whether to use hybrid RAG
     */
    default HybridRagDecision shouldUseHybridRag(Project project, String targetBranch) {
        if (!isRagEnabled(project)) {
            return new HybridRagDecision(false, null, targetBranch, false, "rag_disabled");
        }
        
        String baseBranch = getBaseBranch(project);
        
        // If target is the base branch, no need for hybrid
        if (baseBranch.equals(targetBranch)) {
            return new HybridRagDecision(false, baseBranch, targetBranch, false, "target_is_base");
        }
        
        // Check if delta is enabled and available
        if (!isDeltaIndexEnabled(project)) {
            return new HybridRagDecision(false, baseBranch, targetBranch, false, "delta_disabled");
        }
        
        boolean deltaReady = isDeltaIndexReady(project, targetBranch);
        if (deltaReady) {
            return new HybridRagDecision(true, baseBranch, targetBranch, true, "delta_available");
        } else {
            return new HybridRagDecision(false, baseBranch, targetBranch, false, "delta_not_ready");
        }
    }
    
    /**
     * Ensure delta index exists for a PR target branch if needed.
     * This is called during PR analysis to create delta index on-demand
     * when the target branch should have one but doesn't exist yet.
     * 
     * @param project The project
     * @param targetBranch The PR target branch
     * @param eventConsumer Consumer to receive status updates
     * @return true if delta index is ready (either existed or was created), false otherwise
     */
    default boolean ensureDeltaIndexForPrTarget(
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
     * For PRs targeting branches with delta indexes:
     * - Check if the delta index commit matches the current target branch HEAD
     * - If not, update the delta index with the diff
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
