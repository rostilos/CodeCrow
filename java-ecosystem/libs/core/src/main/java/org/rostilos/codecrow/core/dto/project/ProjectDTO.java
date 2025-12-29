package org.rostilos.codecrow.core.dto.project;

import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;

import java.util.List;

public record ProjectDTO(
        Long id,
        String name,
        String description,
        boolean isActive,
        Long vcsConnectionId,
        String vcsConnectionType,
        String vcsProvider,
        String projectVcsWorkspace,
        String projectVcsRepoSlug,
        Long aiConnectionId,
        String namespace,
        String defaultBranch,
        Long defaultBranchId,
        DefaultBranchStats defaultBranchStats,
        RagConfigDTO ragConfig,
        Boolean prAnalysisEnabled,
        Boolean branchAnalysisEnabled,
        String installationMethod,
        CommentCommandsConfigDTO commentCommandsConfig
) {
    public static ProjectDTO fromProject(Project project) {
        Long vcsConnectionId = null;
        String vcsConnectionType = null;
        String vcsProvider = null;
        String vcsWorkspace = null;
        String repoSlug = null;
        
        // Check vcsBinding first (manual OAuth connection)
        if (project.getVcsBinding() != null && project.getVcsBinding().getVcsConnection() != null) {
            vcsConnectionId = project.getVcsBinding().getVcsConnection().getId();
            vcsWorkspace = project.getVcsBinding().getWorkspace();
            repoSlug = project.getVcsBinding().getRepoSlug();
            if (project.getVcsBinding().getVcsConnection().getConnectionType() != null) {
                vcsConnectionType = project.getVcsBinding().getVcsConnection().getConnectionType().name();
            }
            if (project.getVcsBinding().getVcsConnection().getProviderType() != null) {
                vcsProvider = project.getVcsBinding().getVcsConnection().getProviderType().name();
            }
        }
        // Fallback to vcsRepoBinding (App-based connection)
        else if (project.getVcsRepoBinding() != null) {
            if (project.getVcsRepoBinding().getVcsConnection() != null) {
                vcsConnectionId = project.getVcsRepoBinding().getVcsConnection().getId();
                if (project.getVcsRepoBinding().getVcsConnection().getConnectionType() != null) {
                    vcsConnectionType = project.getVcsRepoBinding().getVcsConnection().getConnectionType().name();
                }
                if (project.getVcsRepoBinding().getVcsConnection().getProviderType() != null) {
                    vcsProvider = project.getVcsRepoBinding().getVcsConnection().getProviderType().name();
                }
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
        // Use entity-level settings as default, then override from config if present
        Boolean prAnalysisEnabled = project.isPrAnalysisEnabled();
        Boolean branchAnalysisEnabled = project.isBranchAnalysisEnabled();
        String installationMethod = null;
        
        ProjectConfig config = project.getConfiguration();
        if (config != null) {
            if (config.ragConfig() != null) {
                ProjectConfig.RagConfig rc = config.ragConfig();
                ragConfigDTO = new RagConfigDTO(rc.enabled(), rc.branch(), rc.excludePatterns());
            }
            if (config.prAnalysisEnabled() != null) {
                prAnalysisEnabled = config.prAnalysisEnabled();
            }
            if (config.branchAnalysisEnabled() != null) {
                branchAnalysisEnabled = config.branchAnalysisEnabled();
            }
            if (config.installationMethod() != null) {
                installationMethod = config.installationMethod().name();
            }
        }
        
        CommentCommandsConfigDTO commentCommandsConfigDTO = null;
        if (config != null) {
            commentCommandsConfigDTO = CommentCommandsConfigDTO.fromConfig(config.getCommentCommandsConfig());
        }

        return new ProjectDTO(
                project.getId(),
                project.getName(),
                project.getDescription(),
                project.getIsActive(),
                vcsConnectionId,
                vcsConnectionType,
                vcsProvider,
                vcsWorkspace,
                repoSlug,
                aiConnectionId,
                project.getNamespace(),
                defaultBranch,
                defaultBranchId,
                stats,
                ragConfigDTO,
                prAnalysisEnabled,
                branchAnalysisEnabled,
                installationMethod,
                commentCommandsConfigDTO
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
    
    public record CommentCommandsConfigDTO(
            boolean enabled,
            Integer rateLimit,
            Integer rateLimitWindowMinutes,
            Boolean allowPublicRepoCommands,
            List<String> allowedCommands,
            String authorizationMode,
            Boolean allowPrAuthor
    ) {
        public static CommentCommandsConfigDTO fromConfig(ProjectConfig.CommentCommandsConfig config) {
            if (config == null) {
                return new CommentCommandsConfigDTO(false, null, null, null, null, null, null);
            }
            String authMode = config.authorizationMode() != null 
                ? config.authorizationMode().name() 
                : ProjectConfig.CommentCommandsConfig.DEFAULT_AUTHORIZATION_MODE.name();
            return new CommentCommandsConfigDTO(
                    config.enabled(),
                    config.rateLimit(),
                    config.rateLimitWindowMinutes(),
                    config.allowPublicRepoCommands(),
                    config.allowedCommands(),
                    authMode,
                    config.allowPrAuthor()
            );
        }
    }
}