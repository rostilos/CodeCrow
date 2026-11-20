package org.rostilos.codecrow.webserver.project.service;

import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.rag.RagBranchIndex;
import org.rostilos.codecrow.core.persistence.repository.rag.RagBranchIndexRepository;
import org.rostilos.codecrow.webserver.project.dto.response.RagBranchIndexStatusDTO;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Read-only projection of lazily observed RAG branches. State and counts come
 * only from the exact branch-generation registry.
 */
@Service
public class RagBranchIndexStatusService {
    private final RagBranchIndexRepository branchIndexRepository;

    public RagBranchIndexStatusService(RagBranchIndexRepository branchIndexRepository) {
        this.branchIndexRepository = branchIndexRepository;
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
        List<RagBranchIndexStatusDTO> result = new ArrayList<>();
        result.add(toDto(primary, "PRIMARY", persisted.get(primary)));
        for (RagBranchIndex index : persisted.values()) {
            if (!primary.equals(index.getBranchName())) {
                result.add(toDto(index.getBranchName(), "TARGET", index));
            }
        }
        return result;
    }

    private RagBranchIndexStatusDTO toDto(
            String branch,
            String role,
            RagBranchIndex index) {
        if (index == null) {
            return new RagBranchIndexStatusDTO(branch, role, "NOT_INDEXED", null, null,
                    null, null, null, null);
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
                generation != null ? generation.getRevision() : null,
                index.getDesiredCommitHash(),
                generation != null ? generation.getFileCount() : null,
                generation != null ? generation.getChunkCount() : null,
                generation != null && generation.getActivatedAt() != null
                        ? generation.getActivatedAt() : index.getUpdatedAt(),
                index.getErrorMessage());
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
