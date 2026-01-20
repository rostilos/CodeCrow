package org.rostilos.codecrow.pipelineagent.generic.webhookhandler;

import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.BranchAnalysisConfig;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.util.BranchPatternMatcher;

import java.util.List;

public abstract class AbstractWebhookHandler {
    protected static String validateProjectConnections(Project project) {
        // Use unified hasVcsBinding() method that checks both bindings
        if (!project.hasVcsBinding()) {
            return "VCS connection is not configured for project: " + project.getId();
        }

        if (project.getAiBinding() == null) {
            return "AI connection is not configured for project: " + project.getId();
        }

        return null;
    }

    /**
     * Check if a branch matches the configured analysis patterns.
     * Note: isBranchAnalysisEnabled() check is done in the handle() method before this is called.
     */
    protected static boolean shouldAnalyze(Project project, String branchName, AnalysisType analysisType) {
        if(analysisType == AnalysisType.BRANCH_ANALYSIS) {
            if (project.getConfiguration() == null) {
                return true;
            }

            BranchAnalysisConfig branchConfig = project.getConfiguration().branchAnalysis();
            if (branchConfig == null) {
                return true;
            }

            List<String> branchPushPatterns = branchConfig.branchPushPatterns();
            return BranchPatternMatcher.shouldAnalyze(branchName, branchPushPatterns);
        }

        if(analysisType == AnalysisType.PR_REVIEW) {
            String targetBranch = branchName;
            if (project.getConfiguration() == null) {
                return true;
            }

            BranchAnalysisConfig branchConfig = project.getConfiguration().branchAnalysis();
            if (branchConfig == null) {
                return true;
            }

            List<String> prTargetBranches = branchConfig.prTargetBranches();
            return BranchPatternMatcher.shouldAnalyze(targetBranch, prTargetBranches);
        }
        return false;
    }
}
