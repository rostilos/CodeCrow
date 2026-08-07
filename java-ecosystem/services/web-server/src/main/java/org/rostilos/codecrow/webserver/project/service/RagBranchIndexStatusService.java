package org.rostilos.codecrow.webserver.project.service;

import org.rostilos.codecrow.core.model.analysis.RagIndexStatus;
import org.rostilos.codecrow.core.model.analysis.RagIndexingStatus;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.rag.RagBranchIndex;
import org.rostilos.codecrow.core.model.rag.RagBranchIndexKind;
import org.rostilos.codecrow.core.persistence.repository.rag.RagBranchIndexRepository;
import org.rostilos.codecrow.webserver.project.dto.response.RagBranchIndexStatusDTO;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Read-only projection of the primary and explicitly retained RAG branches.
 * It intentionally excludes transient PR-only snapshots from the configuration
 * view, while retaining a compatible primary status for pre-generation projects.
 */
@Service
public class RagBranchIndexStatusService {
    private final RagBranchIndexRepository branchIndexRepository;
    private final RagIndexStatusService projectStatusService;

    public RagBranchIndexStatusService(
            RagBranchIndexRepository branchIndexRepository,
            RagIndexStatusService projectStatusService) {
        this.branchIndexRepository = branchIndexRepository;
        this.projectStatusService = projectStatusService;
    }

    @Transactional(readOnly = true)
    public List<RagBranchIndexStatusDTO> getConfiguredBranches(Project project) {
        if (project.getConfiguration() == null || project.getConfiguration().ragConfig() == null) {
            return List.of();
        }
        var config = project.getConfiguration().ragConfig();
        String primary = resolvePrimary(project, config.branch());
        if (primary == null) {
            return List.of();
        }

        Map<String, RagBranchIndex> persisted = new LinkedHashMap<>();
        for (RagBranchIndex index : branchIndexRepository.findByProjectId(project.getId())) {
            persisted.put(index.getBranchName(), index);
        }
        RagIndexStatus projectStatus = projectStatusService.getIndexStatus(project).orElse(null);

        List<RagBranchIndexStatusDTO> result = new ArrayList<>();
        result.add(toDto(primary, "PRIMARY", persisted.get(primary), projectStatus));
        for (String branch : config.getEffectiveIndexedBranches()) {
            if (!primary.equals(branch)) {
                result.add(toDto(branch, "RETAINED", persisted.get(branch), null));
            }
        }
        return result;
    }

    private RagBranchIndexStatusDTO toDto(
            String branch,
            String role,
            RagBranchIndex index,
            RagIndexStatus legacyPrimaryStatus) {
        if (index == null) {
            return legacyPrimaryStatus == null
                    ? new RagBranchIndexStatusDTO(branch, role, "NOT_INDEXED", null, null,
                            null, null, null, null)
                    : legacyPrimaryDto(branch, role, legacyPrimaryStatus);
        }

        var generation = index.getActiveGeneration();
        String status = switch (index.getLifecycleStatus()) {
            case PENDING -> "PENDING";
            case BUILDING -> "BUILDING";
            case FAILED -> "FAILED";
            case READY -> generation == null ? "NOT_INDEXED" : "READY";
        };
        return new RagBranchIndexStatusDTO(
                branch,
                role,
                status,
                generation != null ? generation.getRevision() : index.getCommitHash(),
                index.getDesiredCommitHash(),
                generation != null ? generation.getFileCount() : null,
                generation != null ? generation.getChunkCount() : index.getChunkCount(),
                generation != null && generation.getActivatedAt() != null
                        ? generation.getActivatedAt() : index.getUpdatedAt(),
                index.getErrorMessage());
    }

    private RagBranchIndexStatusDTO legacyPrimaryDto(
            String branch,
            String role,
            RagIndexStatus status) {
        String displayStatus = switch (status.getStatus()) {
            case INDEXING -> "BUILDING";
            case UPDATING -> "BUILDING";
            case INDEXED -> "READY";
            case FAILED -> "FAILED";
            default -> "NOT_INDEXED";
        };
        OffsetDateTime updated = status.getLastIndexedAt() != null
                ? status.getLastIndexedAt() : status.getUpdatedAt();
        return new RagBranchIndexStatusDTO(
                branch, role, displayStatus, status.getIndexedCommitHash(), null,
                status.getTotalFilesIndexed(), status.getChunkCount(), updated,
                status.getErrorMessage());
    }

    private String resolvePrimary(Project project, String configuredBranch) {
        if (configuredBranch != null && !configuredBranch.isBlank()) {
            return configuredBranch.trim();
        }
        if (project.getConfiguration().defaultBranch() != null
                && !project.getConfiguration().defaultBranch().isBlank()) {
            return project.getConfiguration().defaultBranch().trim();
        }
        if (project.getDefaultBranch() != null && project.getDefaultBranch().getBranchName() != null) {
            return project.getDefaultBranch().getBranchName().trim();
        }
        return null;
    }
}
