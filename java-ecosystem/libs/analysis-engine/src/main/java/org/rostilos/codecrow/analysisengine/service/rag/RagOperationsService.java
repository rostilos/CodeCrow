package org.rostilos.codecrow.analysisengine.service.rag;

import org.rostilos.codecrow.core.model.project.Project;

import java.util.function.Consumer;
import java.util.Map;

/**
 * Interface for RAG (Retrieval-Augmented Generation) operations.
 * This interface allows analysis-engine to trigger RAG operations without directly depending on rag-engine.
 * Implementations will be provided by the rag-engine module.
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
     * @param eventConsumer Consumer to receive status updates during processing
     */
    void triggerIncrementalUpdate(
            Project project, 
            String branchName, 
            String commitHash,
            Consumer<Map<String, Object>> eventConsumer
    );
}
