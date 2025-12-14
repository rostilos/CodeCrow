package org.rostilos.codecrow.analysisengine.service;

import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.springframework.stereotype.Service;

import java.io.IOException;

/**
 * Service for managing project operations and validation.
 */
@Service
public class ProjectService {

    private final ProjectRepository projectRepository;

    public ProjectService(ProjectRepository projectRepository) {
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
        // Check for VCS connection: either legacy ProjectVcsConnectionBinding or new VcsRepoBinding
        boolean hasVcsConnection = project.getVcsBinding() != null ||
                (project.getVcsRepoBinding() != null && project.getVcsRepoBinding().getVcsConnection() != null);
        
        if (!hasVcsConnection) {
            throw new IOException("VCS connection is not configured for project: " + project.getId());
        }

        if (project.getAiBinding() == null) {
            throw new IOException("AI connection is not configured for project: " + project.getId());
        }
    }
}
