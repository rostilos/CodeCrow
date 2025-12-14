package org.rostilos.codecrow.ragengine.service;

import org.rostilos.codecrow.core.model.analysis.RagIndexStatus;
import org.rostilos.codecrow.core.model.analysis.RagIndexingStatus;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.persistence.repository.analysis.RagIndexStatusRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.OffsetDateTime;
import java.util.Optional;

@Service
public class RagIndexTrackingService {

    private static final Logger log = LoggerFactory.getLogger(RagIndexTrackingService.class);

    private final RagIndexStatusRepository ragIndexStatusRepository;

    public RagIndexTrackingService(RagIndexStatusRepository ragIndexStatusRepository) {
        this.ragIndexStatusRepository = ragIndexStatusRepository;
    }

    @Transactional(readOnly = true)
    public boolean isProjectIndexed(Project project) {
        return ragIndexStatusRepository.isProjectIndexed(project.getId());
    }

    @Transactional(readOnly = true)
    public Optional<RagIndexStatus> getIndexStatus(Project project) {
        return ragIndexStatusRepository.findByProjectId(project.getId());
    }

    @Transactional
    public RagIndexStatus markIndexingStarted(Project project, String branchName, String commitHash) {
        Optional<RagIndexStatus> existingOpt = ragIndexStatusRepository.findByProjectId(project.getId());

        RagIndexStatus status;
        if (existingOpt.isPresent()) {
            status = existingOpt.get();
            status.setStatus(RagIndexingStatus.INDEXING);
            status.setIndexedBranch(branchName);
            status.setIndexedCommitHash(commitHash);
            status.setErrorMessage(null);
        } else {
            status = new RagIndexStatus();
            status.setProject(project);
            status.setWorkspaceName(project.getWorkspace().getName());
            status.setProjectName(project.getName());
            status.setStatus(RagIndexingStatus.INDEXING);
            status.setIndexedBranch(branchName);
            status.setIndexedCommitHash(commitHash);
            status.setCollectionName(generateCollectionName(project));
        }

        status = ragIndexStatusRepository.save(status);
        log.info("Marked RAG indexing as STARTED for project {} (branch: {})", project.getName(), branchName);
        return status;
    }

    @Transactional
    public RagIndexStatus markIndexingCompleted(Project project, String branchName, String commitHash, Integer filesIndexed) {
        RagIndexStatus status = ragIndexStatusRepository.findByProjectId(project.getId())
                .orElseThrow(() -> new IllegalStateException("RAG index status not found for project: " + project.getId()));

        status.setStatus(RagIndexingStatus.INDEXED);
        status.setIndexedBranch(branchName);
        status.setIndexedCommitHash(commitHash);
        status.setTotalFilesIndexed(filesIndexed);
        status.setLastIndexedAt(OffsetDateTime.now());
        status.setErrorMessage(null);

        status = ragIndexStatusRepository.save(status);
        log.info("Marked RAG indexing as COMPLETED for project {} ({} files)", project.getName(), filesIndexed);
        return status;
    }

    @Transactional
    public RagIndexStatus markIndexingFailed(Project project, String errorMessage) {
        Optional<RagIndexStatus> existingOpt = ragIndexStatusRepository.findByProjectId(project.getId());

        RagIndexStatus status;
        if (existingOpt.isPresent()) {
            status = existingOpt.get();
            status.setStatus(RagIndexingStatus.FAILED);
            status.setErrorMessage(errorMessage);
        } else {
            status = new RagIndexStatus();
            status.setProject(project);
            status.setWorkspaceName(project.getWorkspace().getName());
            status.setProjectName(project.getName());
            status.setStatus(RagIndexingStatus.FAILED);
            status.setErrorMessage(errorMessage);
            status.setCollectionName(generateCollectionName(project));
        }

        status = ragIndexStatusRepository.save(status);
        log.warn("Marked RAG indexing as FAILED for project {}: {}", project.getName(), errorMessage);
        return status;
    }

    @Transactional
    public RagIndexStatus markUpdatingStarted(Project project, String branchName, String commitHash) {
        RagIndexStatus status = ragIndexStatusRepository.findByProjectId(project.getId())
                .orElseThrow(() -> new IllegalStateException("Cannot update non-indexed project: " + project.getId()));

        status.setStatus(RagIndexingStatus.UPDATING);
        status.setIndexedBranch(branchName);
        status.setIndexedCommitHash(commitHash);
        status.setErrorMessage(null);

        status = ragIndexStatusRepository.save(status);
        log.info("Marked RAG indexing as UPDATING for project {}", project.getName());
        return status;
    }

    @Transactional
    public RagIndexStatus markUpdatingCompleted(Project project, String branchName, String commitHash, Integer filesIndexed) {
        RagIndexStatus status = ragIndexStatusRepository.findByProjectId(project.getId())
                .orElseThrow(() -> new IllegalStateException("RAG index status not found for project: " + project.getId()));

        status.setStatus(RagIndexingStatus.INDEXED);
        status.setIndexedBranch(branchName);
        status.setIndexedCommitHash(commitHash);
        status.setTotalFilesIndexed(filesIndexed);
        status.setLastIndexedAt(OffsetDateTime.now());
        status.setErrorMessage(null);

        status = ragIndexStatusRepository.save(status);
        log.info("Marked RAG updating as COMPLETED for project {}", project.getName());
        return status;
    }

    @Transactional(readOnly = true)
    public boolean canStartIndexing(Project project) {
        Optional<RagIndexStatus> statusOpt = ragIndexStatusRepository.findByProjectId(project.getId());

        if (statusOpt.isEmpty()) {
            return true;
        }

        RagIndexStatus status = statusOpt.get();
        return status.getStatus() != RagIndexingStatus.INDEXING &&
               status.getStatus() != RagIndexingStatus.UPDATING;
    }

    private String generateCollectionName(Project project) {
        String workspace = project.getWorkspace().getName().replaceAll("[^a-zA-Z0-9_-]", "_");
        String projectName = project.getName().replaceAll("[^a-zA-Z0-9_-]", "_");
        return String.format("%s_%s", workspace, projectName).toLowerCase();
    }
}
