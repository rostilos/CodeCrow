package org.rostilos.codecrow.webserver.qualitygate.service;

import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.model.qualitygate.QualityGate;
import org.rostilos.codecrow.core.model.qualitygate.QualityGateComparator;
import org.rostilos.codecrow.core.model.qualitygate.QualityGateCondition;
import org.rostilos.codecrow.core.model.qualitygate.QualityGateMetric;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.qualitygate.QualityGateRepository;
import org.rostilos.codecrow.webserver.qualitygate.dto.request.CreateQualityGateRequest;
import org.rostilos.codecrow.webserver.qualitygate.dto.request.QualityGateConditionRequest;
import org.rostilos.codecrow.webserver.qualitygate.dto.request.UpdateQualityGateRequest;
import org.rostilos.codecrow.webserver.workspace.service.WorkspaceService;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.NoSuchElementException;

@Service
public class QualityGateService {

    private final QualityGateRepository qualityGateRepository;
    private final WorkspaceService workspaceService;

    public QualityGateService(
            QualityGateRepository qualityGateRepository,
            WorkspaceService workspaceService
    ) {
        this.qualityGateRepository = qualityGateRepository;
        this.workspaceService = workspaceService;
    }

    public List<QualityGate> listWorkspaceQualityGates(Long workspaceId) {
        return qualityGateRepository.findByWorkspaceId(workspaceId);
    }

    public QualityGate getQualityGate(Long workspaceId, Long qualityGateId) {
        return qualityGateRepository.findByIdAndWorkspaceId(qualityGateId, workspaceId)
                .orElseThrow(() -> new NoSuchElementException("Quality gate not found"));
    }

    public QualityGate getDefaultQualityGate(Long workspaceId) {
        return qualityGateRepository.findByWorkspaceIdAndIsDefaultTrue(workspaceId).orElse(null);
    }

    @Transactional
    public QualityGate createQualityGate(Long workspaceId, CreateQualityGateRequest request) {
        Workspace workspace = workspaceService.getWorkspaceById(workspaceId);

        // If this is set as default, unset other defaults
        if (request.isDefault()) {
            qualityGateRepository.clearDefaultForWorkspace(workspaceId);
        }

        QualityGate qualityGate = new QualityGate();
        qualityGate.setWorkspace(workspace);
        qualityGate.setName(request.getName());
        qualityGate.setDescription(request.getDescription());
        qualityGate.setDefault(request.isDefault());
        qualityGate.setActive(request.isActive());

        for (QualityGateConditionRequest condRequest : request.getConditions()) {
            QualityGateCondition condition = createConditionFromRequest(condRequest);
            qualityGate.addCondition(condition);
        }

        return qualityGateRepository.save(qualityGate);
    }

    @Transactional
    public QualityGate updateQualityGate(Long workspaceId, Long qualityGateId, UpdateQualityGateRequest request) {
        QualityGate qualityGate = getQualityGate(workspaceId, qualityGateId);

        if (request.getName() != null) {
            qualityGate.setName(request.getName());
        }
        if (request.getDescription() != null) {
            qualityGate.setDescription(request.getDescription());
        }
        if (request.isDefault() != null) {
            if (request.isDefault() && !qualityGate.isDefault()) {
                qualityGateRepository.clearDefaultForWorkspace(workspaceId);
            }
            qualityGate.setDefault(request.isDefault());
        }
        if (request.isActive() != null) {
            qualityGate.setActive(request.isActive());
        }

        // Update conditions if provided
        if (request.getConditions() != null) {
            qualityGate.getConditions().clear();
            for (QualityGateConditionRequest condRequest : request.getConditions()) {
                QualityGateCondition condition = createConditionFromRequest(condRequest);
                qualityGate.addCondition(condition);
            }
        }

        return qualityGateRepository.save(qualityGate);
    }

    @Transactional
    public void deleteQualityGate(Long workspaceId, Long qualityGateId) {
        QualityGate qualityGate = getQualityGate(workspaceId, qualityGateId);
        qualityGateRepository.delete(qualityGate);
    }

    @Transactional
    public QualityGate setDefault(Long workspaceId, Long qualityGateId) {
        QualityGate qualityGate = getQualityGate(workspaceId, qualityGateId);
        qualityGateRepository.clearDefaultForWorkspace(workspaceId);
        qualityGate.setDefault(true);
        return qualityGateRepository.save(qualityGate);
    }

    private QualityGateCondition createConditionFromRequest(QualityGateConditionRequest request) {
        QualityGateCondition condition = new QualityGateCondition();
        condition.setMetric(request.getMetric());
        condition.setSeverity(request.getSeverity());
        condition.setCategory(request.getCategory());
        condition.setComparator(request.getComparator());
        condition.setThresholdValue(request.getThresholdValue());
        condition.setEnabled(request.isEnabled());
        return condition;
    }

    /**
     * Creates a default quality gate for a workspace.
     * Default rules:
     * - HIGH issues > 0 = FAIL
     * - MEDIUM issues > 0 = FAIL
     * - LOW and INFO issues are allowed (not checked by default)
     */
    @Transactional
    public QualityGate createDefaultQualityGate(Workspace workspace) {
        QualityGate qualityGate = new QualityGate();
        qualityGate.setWorkspace(workspace);
        qualityGate.setName("Default Quality Gate");
        qualityGate.setDescription("Fails if any HIGH or MEDIUM severity issues are found");
        qualityGate.setDefault(true);
        qualityGate.setActive(true);

        // HIGH issues > 0 = FAIL
        QualityGateCondition highCondition = new QualityGateCondition();
        highCondition.setMetric(QualityGateMetric.ISSUES_BY_SEVERITY);
        highCondition.setSeverity(IssueSeverity.HIGH);
        highCondition.setComparator(QualityGateComparator.GREATER_THAN);
        highCondition.setThresholdValue(0);
        highCondition.setEnabled(true);
        qualityGate.addCondition(highCondition);

        // MEDIUM issues > 0 = FAIL
        QualityGateCondition mediumCondition = new QualityGateCondition();
        mediumCondition.setMetric(QualityGateMetric.ISSUES_BY_SEVERITY);
        mediumCondition.setSeverity(IssueSeverity.MEDIUM);
        mediumCondition.setComparator(QualityGateComparator.GREATER_THAN);
        mediumCondition.setThresholdValue(0);
        mediumCondition.setEnabled(true);
        qualityGate.addCondition(mediumCondition);

        return qualityGateRepository.save(qualityGate);
    }

    /**
     * Ensures a workspace has a default quality gate.
     * Creates one if it doesn't exist.
     * Handles concurrent creation attempts gracefully using a new transaction.
     */
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public QualityGate ensureDefaultQualityGate(Long workspaceId) {
        // Check again within new transaction (double-check locking pattern)
        QualityGate existing = getDefaultQualityGate(workspaceId);
        if (existing != null) {
            return existing;
        }
        
        try {
            Workspace workspace = workspaceService.getWorkspaceById(workspaceId);
            return createDefaultQualityGate(workspace);
        } catch (org.springframework.dao.DataIntegrityViolationException e) {
            // Race condition: another thread created the default gate concurrently
            // This shouldn't happen often due to the check above, but handle it gracefully
            // Since we're in a new transaction, we can safely query again after rollback
            throw new RuntimeException("Default quality gate was created by another process. Please retry.", e);
        }
    }
}
