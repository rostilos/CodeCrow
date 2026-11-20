package org.rostilos.codecrow.analysisengine.dto.request.ai;

/**
 * Ephemeral local repository snapshot made available to one inference job.
 *
 * <p>The path is execution metadata and is deliberately kept separate from
 * {@link AiAnalysisRequest}, whose fields participate in review identity and
 * caching.</p>
 */
public record LocalRepositorySnapshot(
        String path,
        String targetBranch,
        String revision,
        String reviewOverlayPath
) {
    public LocalRepositorySnapshot(String path, String targetBranch, String revision) {
        this(path, targetBranch, revision, null);
    }
}
