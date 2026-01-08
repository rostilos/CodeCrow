package org.rostilos.codecrow.webserver.project.service;

import org.rostilos.codecrow.core.model.analysis.RagIndexStatus;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.persistence.repository.analysis.RagIndexStatusRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.Optional;

/**
 * Service for accessing RAG indexing status in web-server.
 * Note: The actual indexing is performed by pipeline-agent.
 */
@Service
public class RagIndexStatusService {

    private final RagIndexStatusRepository ragIndexStatusRepository;

    public RagIndexStatusService(RagIndexStatusRepository ragIndexStatusRepository) {
        this.ragIndexStatusRepository = ragIndexStatusRepository;
    }

    @Transactional(readOnly = true)
    public Optional<RagIndexStatus> getIndexStatus(Project project) {
        return ragIndexStatusRepository.findByProjectId(project.getId());
    }

    @Transactional(readOnly = true)
    public boolean isProjectIndexed(Long projectId) {
        return ragIndexStatusRepository.isProjectIndexed(projectId);
    }

    @Transactional(readOnly = true)
    public boolean canStartIndexing(Project project) {
        Optional<RagIndexStatus> statusOpt = ragIndexStatusRepository.findByProjectId(project.getId());

        if (statusOpt.isEmpty()) {
            return true;
        }

        RagIndexStatus status = statusOpt.get();
        return status.getStatus() != org.rostilos.codecrow.core.model.analysis.RagIndexingStatus.INDEXING &&
               status.getStatus() != org.rostilos.codecrow.core.model.analysis.RagIndexingStatus.UPDATING;
    }
}
