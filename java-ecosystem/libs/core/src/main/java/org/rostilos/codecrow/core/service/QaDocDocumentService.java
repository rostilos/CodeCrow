package org.rostilos.codecrow.core.service;

import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.qadoc.QaDocDocument;
import org.rostilos.codecrow.core.persistence.repository.qadoc.QaDocDocumentRepository;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.Optional;

@Service
@Transactional
public class QaDocDocumentService {

    private final QaDocDocumentRepository qaDocDocumentRepository;

    public QaDocDocumentService(QaDocDocumentRepository qaDocDocumentRepository) {
        this.qaDocDocumentRepository = qaDocDocumentRepository;
    }

    public QaDocDocument upsertLatestDocument(Project project,
                                              Long prNumber,
                                              String taskId,
                                              Long analysisId,
                                              String commitHash,
                                              String markdownContent) {
        if (project == null || project.getId() == null) {
            throw new IllegalArgumentException("Project is required to persist QA documentation.");
        }
        if (prNumber == null) {
            throw new IllegalArgumentException("PR number is required to persist QA documentation.");
        }
        if (markdownContent == null || markdownContent.isBlank()) {
            throw new IllegalArgumentException("Markdown content is required to persist QA documentation.");
        }

        QaDocDocument document = qaDocDocumentRepository
                .findByProjectIdAndPrNumber(project.getId(), prNumber)
                .orElseGet(() -> new QaDocDocument(project, prNumber));

        document.replaceContent(taskId, analysisId, commitHash, markdownContent);
        return qaDocDocumentRepository.save(document);
    }

    @Transactional(readOnly = true)
    public Optional<QaDocDocument> findLatestDocument(Long projectId, Long prNumber) {
        if (projectId == null || prNumber == null) {
            return Optional.empty();
        }
        return qaDocDocumentRepository.findByProjectIdAndPrNumber(projectId, prNumber);
    }

    @Transactional(readOnly = true)
    public Optional<QaDocDocument> findLatestDocumentForTask(Long projectId,
                                                             String taskId,
                                                             Long excludedPrNumber) {
        if (projectId == null || taskId == null || taskId.isBlank()) {
            return Optional.empty();
        }
        return findDocumentsForTask(projectId, taskId, excludedPrNumber, 1)
                .stream()
                .findFirst();
    }

    @Transactional(readOnly = true)
    public List<QaDocDocument> findDocumentsForTask(Long projectId, String taskId) {
        if (projectId == null || taskId == null || taskId.isBlank()) {
            return List.of();
        }
        return qaDocDocumentRepository.findByProjectIdAndTaskIdOrderByGeneratedAtDesc(projectId, taskId);
    }

    @Transactional(readOnly = true)
    public List<QaDocDocument> findDocumentsForTask(Long projectId,
                                                    String taskId,
                                                    Long excludedPrNumber,
                                                    int limit) {
        if (projectId == null || taskId == null || taskId.isBlank() || limit <= 0) {
            return List.of();
        }
        PageRequest pageRequest = PageRequest.of(0, limit);
        if (excludedPrNumber == null) {
            return qaDocDocumentRepository.findByProjectIdAndTaskIdOrderByGeneratedAtDesc(
                    projectId,
                    taskId,
                    pageRequest);
        }
        return qaDocDocumentRepository.findByProjectIdAndTaskIdAndPrNumberNotOrderByGeneratedAtDesc(
                projectId,
                taskId,
                excludedPrNumber,
                pageRequest);
    }
}
