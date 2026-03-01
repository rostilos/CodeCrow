package org.rostilos.codecrow.analysisengine.service;

import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.springframework.stereotype.Service;

import java.io.IOException;

/**
 * Service for project retrieval and connection validation.
 * Validates that VCS and AI connections are properly configured before analysis.
 */
@Service
public class ProjectValidationService {

    private final ProjectRepository projectRepository;

    public ProjectValidationService(ProjectRepository projectRepository) {
        this.projectRepository = projectRepository;
    }

    /**
     * Retrieves a project with all necessary connections validated.
     * Eagerly fetches VCS and AI connections to avoid LazyInitializationException.
     *
     * @param projectId The project identifier
     * @return The project with validated connections
     * @throws IOException if project not found or connections not configured
     */
    public Project getProjectWithConnections(Long projectId) throws IOException {
        Project project = projectRepository.findByIdWithFullDetails(projectId)
                .orElseThrow(() -> new IOException(
                        "Project doesn't exist or authorization has not been passed"
                ));

        validateProjectConnections(project);

        return project;
    }

    private void validateProjectConnections(Project project) throws IOException {
        // Use unified hasVcsBinding() method that checks both bindings
        if (!project.hasVcsBinding()) {
            throw new IOException("VCS connection is not configured for project: " + project.getId());
        }

        if (project.getAiBinding() == null) {
            throw new IOException("AI connection is not configured for project: " + project.getId());
        }
    }
}
