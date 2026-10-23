package org.rostilos.codecrow.core.service;

import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.project.Project;
import org.springframework.stereotype.Service;

/** Host extension for execution credentials. Resolved connections must never be serialized to clients. */
@Service
public class AIConnectionResolver {
    public AIConnection resolve(Project project) {
        return project.getAiBinding() == null ? null : project.getAiBinding().getAiConnection();
    }

    public boolean isConfigured(Project project) { return project.getAiBinding() != null; }

    public boolean allowEnvironmentFallback(Project project) { return true; }
}
