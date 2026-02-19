package org.rostilos.codecrow.core.dto.project;

import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.CommentCommandsConfig;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.project.config.RagConfig;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;

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
        String mainBranch,
        String defaultBranch, // Deprecated: use mainBranch. Kept for backward compatibility.
        Long defaultBranchId,
        DefaultBranchStats defaultBranchStats,
        RagConfigDTO ragConfig,
        Boolean prAnalysisEnabled,
        Boolean branchAnalysisEnabled,
        String installationMethod,
        CommentCommandsConfigDTO commentCommandsConfig,
        Boolean webhooksConfigured,
        Long qualityGateId,
        Integer maxAnalysisTokenLimit,
        Boolean useMcpTools) {
    public static ProjectDTO fromProject(Project project) {
        Long vcsConnectionId = null;
        String vcsConnectionType = null;
        String vcsProvider = null;
        String vcsWorkspace = null;
        String repoSlug = null;

        // Use unified method to get VCS info (prefers VcsRepoBinding over legacy
        // vcsBinding)
        VcsRepoInfo vcsInfo = project.getEffectiveVcsRepoInfo();
        if (vcsInfo != null) {
            VcsConnection conn = vcsInfo.getVcsConnection();
            if (conn != null) {
                vcsConnectionId = conn.getId();
                if (conn.getConnectionType() != null) {
                    vcsConnectionType = conn.getConnectionType().name();
                }
                if (conn.getProviderType() != null) {
                    vcsProvider = conn.getProviderType().name();
                }
            }
            vcsWorkspace = vcsInfo.getRepoWorkspace();
            repoSlug = vcsInfo.getRepoSlug();
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
                    branch.getResolvedCount());
        }

        String mainBranch = null;
        RagConfigDTO ragConfigDTO = null;
        // Use entity-level settings as default, then override from config if present
        Boolean prAnalysisEnabled = project.isPrAnalysisEnabled();
        Boolean branchAnalysisEnabled = project.isBranchAnalysisEnabled();
        String installationMethod = null;
        Boolean useMcpTools = false;

        ProjectConfig config = project.getConfiguration();
        if (config != null) {
            mainBranch = config.mainBranch();

            if (config.ragConfig() != null) {
                RagConfig rc = config.ragConfig();
                ragConfigDTO = new RagConfigDTO(
                        rc.enabled(),
                        rc.branch(),
                        rc.includePatterns(),
                        rc.excludePatterns(),
                        rc.multiBranchEnabled(),
                        rc.branchRetentionDays());
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
            useMcpTools = config.useMcpTools();
        }

        CommentCommandsConfigDTO commentCommandsConfigDTO = null;
        if (config != null) {
            commentCommandsConfigDTO = CommentCommandsConfigDTO.fromConfig(config.getCommentCommandsConfig());
        }

        // Get webhooksConfigured from VcsRepoBinding
        Boolean webhooksConfigured = null;
        if (project.getVcsRepoBinding() != null) {
            webhooksConfigured = project.getVcsRepoBinding().isWebhooksConfigured();
        }

        // Get maxAnalysisTokenLimit from config
        Integer maxAnalysisTokenLimit = config != null ? config.maxAnalysisTokenLimit()
                : ProjectConfig.DEFAULT_MAX_ANALYSIS_TOKEN_LIMIT;

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
                mainBranch,
                defaultBranch,
                defaultBranchId,
                stats,
                ragConfigDTO,
                prAnalysisEnabled,
                branchAnalysisEnabled,
                installationMethod,
                commentCommandsConfigDTO,
                webhooksConfigured,
                project.getQualityGate() != null ? project.getQualityGate().getId() : null,
                maxAnalysisTokenLimit,
                useMcpTools);
    }

    public record DefaultBranchStats(
            String branchName,
            int totalIssues,
            int highSeverityCount,
            int mediumSeverityCount,
            int lowSeverityCount,
            int resolvedCount) {
    }

    public record RagConfigDTO(
            boolean enabled,
            String branch,
            java.util.List<String> includePatterns,
            java.util.List<String> excludePatterns,
            Boolean multiBranchEnabled,
            Integer branchRetentionDays) {
        /**
         * Backward-compatible constructor without include patterns and multi-branch
         * fields.
         */
        public RagConfigDTO(boolean enabled, String branch, java.util.List<String> excludePatterns) {
            this(enabled, branch, null, excludePatterns, null, null);
        }
    }

    public record CommentCommandsConfigDTO(
            boolean enabled,
            Integer rateLimit,
            Integer rateLimitWindowMinutes,
            Boolean allowPublicRepoCommands,
            List<String> allowedCommands,
            String authorizationMode,
            Boolean allowPrAuthor) {
        public static CommentCommandsConfigDTO fromConfig(CommentCommandsConfig config) {
            if (config == null) {
                return new CommentCommandsConfigDTO(false, null, null, null, null, null, null);
            }
            String authMode = config.authorizationMode() != null
                    ? config.authorizationMode().name()
                    : CommentCommandsConfig.DEFAULT_AUTHORIZATION_MODE.name();
            return new CommentCommandsConfigDTO(
                    config.enabled(),
                    config.rateLimit(),
                    config.rateLimitWindowMinutes(),
                    config.allowPublicRepoCommands(),
                    config.allowedCommands(),
                    authMode,
                    config.allowPrAuthor());
        }
    }
}