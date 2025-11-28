package org.rostilos.codecrow.core.dto.project;

import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.project.Project;

public record ProjectDTO(
        Long id,
        String name,
        String description,
        boolean isActive,
        Long vcsConnectionId,
        String projectVcsWorkspace,
        String projectVcsRepoSlug,
        Long aiConnectionId,
        String namespace,
        String defaultBranch,
        Long defaultBranchId,
        DefaultBranchStats defaultBranchStats
) {
    public static ProjectDTO fromProject(Project project) {
        Long vcsConnectionId = null;
        String vcsWorkspace = null;
        String repoSlug = null;
        if (project.getVcsBinding() != null && project.getVcsBinding().getVcsConnection() != null) {
            vcsConnectionId = project.getVcsBinding().getVcsConnection().getId();
            vcsWorkspace = project.getVcsBinding().getWorkspace();
            repoSlug = project.getVcsBinding().getRepoSlug();
        }

        Long aiConnectionId = null;
        if (project.getAiBinding() != null && project.getAiBinding().getAiConnection() != null) {
            aiConnectionId = project.getAiBinding().getAiConnection().getId();
        }

        Long defaultBranchId = null;
        String defaultBranch = null;
        DefaultBranchStats stats = null;
        if (project.getDefaultBranch() != null) {
            Branch branch = project.getDefaultBranch();
            defaultBranchId = branch.getId();
            defaultBranch = branch.getBranchName();

            stats = new DefaultBranchStats(
                    branch.getBranchName(),
                    branch.getTotalIssues(),
                    branch.getHighSeverityCount(),
                    branch.getMediumSeverityCount(),
                    branch.getLowSeverityCount(),
                    branch.getResolvedCount()
            );
        }

        return new ProjectDTO(
                project.getId(),
                project.getName(),
                project.getDescription(),
                project.getIsActive(),
                vcsConnectionId,
                vcsWorkspace,
                repoSlug,
                aiConnectionId,
                project.getNamespace(),
                defaultBranch,
                defaultBranchId,
                stats
        );
    }

    public record DefaultBranchStats(
            String branchName,
            int totalIssues,
            int highSeverityCount,
            int mediumSeverityCount,
            int lowSeverityCount,
            int resolvedCount
    ) {
    }
}