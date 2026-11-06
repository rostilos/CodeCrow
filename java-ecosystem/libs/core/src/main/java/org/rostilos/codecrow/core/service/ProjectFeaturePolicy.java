package org.rostilos.codecrow.core.service;

import org.rostilos.codecrow.core.model.project.Project;
import org.springframework.stereotype.Service;

/** Optional host capabilities; the public host enables every project feature. */
@Service
public class ProjectFeaturePolicy {
    public boolean qaDocumentationEnabled(Project project) { return true; }

    public void requireQaDocumentation(Project project) {
        if (!qaDocumentationEnabled(project)) throw new FeatureUnavailableException("QA documentation is unavailable for this workspace");
    }

    public static class FeatureUnavailableException extends RuntimeException {
        public FeatureUnavailableException(String message) { super(message); }
    }
}
