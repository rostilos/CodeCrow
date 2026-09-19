package org.rostilos.codecrow.analysisapi.rag;

import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.util.BranchPatternMatcher;

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
     * Check if the RAG pipeline service is reachable (health check).
     * Used to verify connectivity before attempting RAG operations.
     *
     * @return true if the RAG pipeline is healthy and reachable, false otherwise
     */
    default boolean isRagPipelineHealthy() {
        return true; // Default assumes healthy; implementations should do actual check
    }

    /** Build and atomically activate a complete immutable generation for a revision. */
    boolean refreshBranchGeneration(
            Project project,
            String branchName,
            String revision,
            Consumer<Map<String, Object>> eventConsumer);

    /** Execute one already claimed durable repository-index queue job. */
    default boolean executeQueuedBranchGeneration(
            Project project,
            String branchName,
            String revision,
            Job queuedJob) {
        return false;
    }
    
    /**
     * Check whether an observed branch belongs to either configured analysis
     * surface. Branch snapshots are created lazily from real PR/branch events;
     * wildcard patterns are never enumerated eagerly.
     *
     * @param project The project to check
     * @param branchName The branch name to evaluate
     * @return true if the branch is an eligible PR target or push-analysis branch
     */
    default boolean shouldHaveBranchIndex(Project project, String branchName) {
        var config = project.getConfiguration();
        if (branchName == null || branchName.isBlank()) {
            return false;
        }
        if (config == null || config.branchAnalysis() == null) {
            return true;
        }
        var branchConfig = config.branchAnalysis();
        return BranchPatternMatcher.shouldAnalyze(
                    branchName, branchConfig.prTargetBranches())
                || BranchPatternMatcher.shouldAnalyze(
                    branchName, branchConfig.branchPushPatterns());
    }
    
    /**
     * Get the authoritative base branch for RAG indexing.
     * 
     * @param project The project
     * @return The configured or provider-reported base branch
     * @throws IllegalStateException when no authoritative branch identity exists
     */
    default String getBaseBranch(Project project) {
        if (project == null) {
            throw new IllegalStateException("Cannot resolve RAG base branch without a project");
        }

        var config = project.getConfiguration();
        if (config != null && config.ragConfig() != null) {
            String ragBranch = normalizeBranch(config.ragConfig().branch());
            if (ragBranch != null) {
                return ragBranch;
            }
        }

        if (config != null) {
            String configuredBranch = normalizeBranch(config.defaultBranch());
            if (configuredBranch != null) {
                return configuredBranch;
            }
        }

        if (project.getVcsRepoBinding() != null) {
            String repositoryBranch = normalizeBranch(
                    project.getVcsRepoBinding().getDefaultBranch());
            if (repositoryBranch != null) {
                return repositoryBranch;
            }
        }

        if (project.getDefaultBranch() != null) {
            String persistedBranch = normalizeBranch(
                    project.getDefaultBranch().getBranchName());
            if (persistedBranch != null) {
                return persistedBranch;
            }
        }

        throw new IllegalStateException(
                "No authoritative RAG base branch is configured for project: "
                        + project.getId());
    }

    private static String normalizeBranch(String branch) {
        if (branch == null) {
            return null;
        }
        String normalized = branch.trim();
        return normalized.isEmpty() ? null : normalized;
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
    
}
