package org.rostilos.codecrow.pipelineagent.qadoc;

import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.PrEnrichmentDataDto;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.config.QaAutoDocConfig;
import org.rostilos.codecrow.taskmanagement.model.TaskDetails;

import java.util.List;

/**
 * Immutable context object that carries all the data needed for a single
 * QA auto-documentation generation request. Using a record avoids an
 * ever-growing parameter list on the generation method.
 */
public record QaDocGenerationContext(
        // ── Core analysis context ────────────────────────────────
        /** QA auto-doc configuration (template mode, regex, etc.). */
        QaAutoDocConfig qaConfig,
        /** Task details from Jira (key, summary, description, priority, …). May be null. */
        TaskDetails taskDetails,
        /** The code analysis with eagerly-loaded issues. May be null. */
        CodeAnalysis analysis,

        // ── Diff context ─────────────────────────────────────────
        /** Full raw unified diff from the VCS platform. May be null. */
        String diff,
        /** Delta diff between last-analyzed commit and current commit (same-PR re-runs). May be null. */
        String deltaDiff,

        // ── Enrichment data ──────────────────────────────────────
        /** Pre-computed file contents + AST metadata + dependency graph. May be null. */
        PrEnrichmentDataDto enrichmentData,
        /** File paths extracted from the diff (for RAG queries). May be null/empty. */
        List<String> changedFilePaths,

        // ── State / mode flags ───────────────────────────────────
        /** Existing QA doc comment body from an earlier PR on the same task (for merging). May be null. */
        String previousDocumentation,
        /** True when the current PR was already documented and this is a re-analysis (e.g., new commits pushed). */
        boolean isSamePrRerun,

        // ── VCS identification (for Python-side RAG queries) ─────
        /** VCS provider key: "bitbucket_cloud", "github", "gitlab". May be null. */
        String vcsProvider,
        /** Workspace slug / org owner / namespace. May be null. */
        String workspaceSlug,
        /** Repository slug. May be null. */
        String repoSlug,
        /** Source branch of the PR. May be null. */
        String sourceBranch,
        /** Target branch of the PR. May be null. */
        String targetBranch,

        // ── VCS credentials (for Python-side RAG deterministic API) ──
        /** OAuth consumer key (Bitbucket). May be null. */
        String oauthKey,
        /** OAuth consumer secret (Bitbucket). May be null. */
        String oauthSecret,
        /** Bearer / PAT token (GitHub, GitLab, Bitbucket APP). May be null. */
        String bearerToken
) {
    /**
     * Builder for constructing a context step-by-step as data becomes available.
     */
    public static Builder builder() {
        return new Builder();
    }

    public static final class Builder {
        private QaAutoDocConfig qaConfig;
        private TaskDetails taskDetails;
        private CodeAnalysis analysis;
        private String diff;
        private String deltaDiff;
        private PrEnrichmentDataDto enrichmentData;
        private List<String> changedFilePaths;
        private String previousDocumentation;
        private boolean isSamePrRerun;
        private String vcsProvider;
        private String workspaceSlug;
        private String repoSlug;
        private String sourceBranch;
        private String targetBranch;
        private String oauthKey;
        private String oauthSecret;
        private String bearerToken;

        private Builder() {}

        public Builder qaConfig(QaAutoDocConfig v) { this.qaConfig = v; return this; }
        public Builder taskDetails(TaskDetails v) { this.taskDetails = v; return this; }
        public Builder analysis(CodeAnalysis v) { this.analysis = v; return this; }
        public Builder diff(String v) { this.diff = v; return this; }
        public Builder deltaDiff(String v) { this.deltaDiff = v; return this; }
        public Builder enrichmentData(PrEnrichmentDataDto v) { this.enrichmentData = v; return this; }
        public Builder changedFilePaths(List<String> v) { this.changedFilePaths = v; return this; }
        public Builder previousDocumentation(String v) { this.previousDocumentation = v; return this; }
        public Builder isSamePrRerun(boolean v) { this.isSamePrRerun = v; return this; }
        public Builder vcsProvider(String v) { this.vcsProvider = v; return this; }
        public Builder workspaceSlug(String v) { this.workspaceSlug = v; return this; }
        public Builder repoSlug(String v) { this.repoSlug = v; return this; }
        public Builder sourceBranch(String v) { this.sourceBranch = v; return this; }
        public Builder targetBranch(String v) { this.targetBranch = v; return this; }
        public Builder oauthKey(String v) { this.oauthKey = v; return this; }
        public Builder oauthSecret(String v) { this.oauthSecret = v; return this; }
        public Builder bearerToken(String v) { this.bearerToken = v; return this; }

        public QaDocGenerationContext build() {
            return new QaDocGenerationContext(
                    qaConfig, taskDetails, analysis,
                    diff, deltaDiff,
                    enrichmentData, changedFilePaths,
                    previousDocumentation, isSamePrRerun,
                    vcsProvider, workspaceSlug, repoSlug, sourceBranch, targetBranch,
                    oauthKey, oauthSecret, bearerToken);
        }
    }
}
