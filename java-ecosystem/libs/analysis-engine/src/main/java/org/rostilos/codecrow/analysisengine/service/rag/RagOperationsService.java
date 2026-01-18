package org.rostilos.codecrow.analysisengine.service.rag;

/**
 * Re-export of the RagOperationsService interface from analysis-api module.
 * 
 * @deprecated Use {@link org.rostilos.codecrow.analysisapi.rag.RagOperationsService} directly.
 *             This interface is kept for backward compatibility during migration.
 */
@Deprecated(since = "1.0", forRemoval = true)
public interface RagOperationsService extends org.rostilos.codecrow.analysisapi.rag.RagOperationsService {
    // This interface now extends the canonical interface from analysis-api
    // Keeping it here for backward compatibility with existing code
}
