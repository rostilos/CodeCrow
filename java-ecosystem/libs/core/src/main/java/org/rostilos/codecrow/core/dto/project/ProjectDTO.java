package org.rostilos.codecrow.core.dto.project;

import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;

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
        DefaultBranchStats defaultBranchStats,
        RagConfigDTO ragConfig
) {
    public static ProjectDTO fromProject(Project project) {
        Long vcsConnectionId = null;
        String vcsWorkspace = null;
        String repoSlug = null;
        
        // Check vcsBinding first (manual OAuth connection)
        if (project.getVcsBinding() != null && project.getVcsBinding().getVcsConnection() != null) {
            vcsConnectionId = project.getVcsBinding().getVcsConnection().getId();
            vcsWorkspace = project.getVcsBinding().getWorkspace();
            repoSlug = project.getVcsBinding().getRepoSlug();
        }
        // Fallback to vcsRepoBinding (App-based connection)
        else if (project.getVcsRepoBinding() != null) {
            if (project.getVcsRepoBinding().getVcsConnection() != null) {
                vcsConnectionId = project.getVcsRepoBinding().getVcsConnection().getId();
            }
            vcsWorkspace = project.getVcsRepoBinding().getExternalNamespace();
            repoSlug = project.getVcsRepoBinding().getExternalRepoSlug();
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

        RagConfigDTO ragConfigDTO = null;
        ProjectConfig config = project.getConfiguration();
        if (config != null && config.ragConfig() != null) {
            ProjectConfig.RagConfig rc = config.ragConfig();
            ragConfigDTO = new RagConfigDTO(rc.enabled(), rc.branch(), rc.excludePatterns());
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
                stats,
                ragConfigDTO
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

    public record RagConfigDTO(
            boolean enabled,
            String branch,
            java.util.List<String> excludePatterns
    ) {
    }
}