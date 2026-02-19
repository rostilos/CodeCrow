package org.rostilos.codecrow.analysisengine.dto.request.ai;

import com.fasterxml.jackson.annotation.JsonProperty;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.PrEnrichmentDataDto;
import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.ai.AIProviderKey;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisMode;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.ProjectVcsConnectionBinding;

import java.util.List;
import java.util.Optional;

public class AiAnalysisRequestImpl implements AiAnalysisRequest {
    protected final Long projectId;
    protected final String projectWorkspace;
    protected final String projectNamespace;
    protected final String projectVcsWorkspace;
    protected final String projectVcsRepoSlug;
    protected final AIProviderKey aiProvider;
    protected final String aiModel;
    protected final String aiApiKey;
    protected final Long pullRequestId;
    @JsonProperty("oAuthClient")
    protected final String oAuthClient;
    @JsonProperty("oAuthSecret")
    protected final String oAuthSecret;
    @JsonProperty("accessToken")
    protected final String accessToken;
    protected final int maxAllowedTokens;
    protected final List<AiRequestPreviousIssueDTO> previousCodeAnalysisIssues;
    protected final boolean useLocalMcp;
    protected final boolean useMcpTools;
    protected final AnalysisType analysisType;
    protected final String prTitle;
    protected final String prDescription;
    protected final List<String> changedFiles;
    protected final List<String> diffSnippets;
    protected final String targetBranchName;
    protected final String vcsProvider;
    protected final String rawDiff;

    // Incremental analysis fields
    protected final AnalysisMode analysisMode;
    protected final String deltaDiff;
    protected final String previousCommitHash;
    protected final String currentCommitHash;

    // File enrichment data (full file contents + dependency graph)
    protected final PrEnrichmentDataDto enrichmentData;

    protected AiAnalysisRequestImpl(Builder<?> builder) {
        this.projectId = builder.projectId;
        this.projectVcsWorkspace = builder.projectVcsWorkspace;
        this.projectVcsRepoSlug = builder.projectVcsRepoSlug;
        this.aiProvider = builder.aiProvider;
        this.aiModel = builder.aiModel;
        this.aiApiKey = builder.aiApiKey;
        this.pullRequestId = builder.pullRequestId;
        this.oAuthClient = builder.oAuthClient;
        this.oAuthSecret = builder.oAuthSecret;
        this.accessToken = builder.accessToken;
        this.maxAllowedTokens = builder.maxAllowedTokens;
        this.previousCodeAnalysisIssues = builder.previousCodeAnalysisIssues;
        this.useLocalMcp = builder.useLocalMcp;
        this.useMcpTools = builder.useMcpTools;
        this.analysisType = builder.analysisType;
        this.prTitle = builder.prTitle;
        this.prDescription = builder.prDescription;
        this.changedFiles = builder.changedFiles;
        this.diffSnippets = builder.diffSnippets;
        this.projectWorkspace = builder.projectWorkspace;
        this.projectNamespace = builder.projectNamespace;
        this.targetBranchName = builder.targetBranchName;
        this.vcsProvider = builder.vcsProvider;
        this.rawDiff = builder.rawDiff;
        // Incremental analysis fields
        this.analysisMode = builder.analysisMode != null ? builder.analysisMode : AnalysisMode.FULL;
        this.deltaDiff = builder.deltaDiff;
        this.previousCommitHash = builder.previousCommitHash;
        this.currentCommitHash = builder.currentCommitHash;
        // File enrichment data
        this.enrichmentData = builder.enrichmentData;
    }

    public Long getProjectId() {
        return projectId;
    }

    public String getProjectVcsWorkspace() {
        return projectVcsWorkspace;
    }

    public String getProjectVcsRepoSlug() {
        return projectVcsRepoSlug;
    }

    public AIProviderKey getAiProvider() {
        return aiProvider;
    }

    public String getAiModel() {
        return aiModel;
    }

    public String getAiApiKey() {
        return aiApiKey;
    }

    public Long getPullRequestId() {
        return pullRequestId;
    }

    @JsonProperty("oAuthClient")
    public String getOAuthClient() {
        return oAuthClient;
    }

    @JsonProperty("oAuthSecret")
    public String getOAuthSecret() {
        return oAuthSecret;
    }

    @JsonProperty("accessToken")
    public String getAccessToken() {
        return accessToken;
    }

    public int getMaxAllowedTokens() {
        return maxAllowedTokens;
    }

    public List<AiRequestPreviousIssueDTO> getPreviousCodeAnalysisIssues() {
        return previousCodeAnalysisIssues;
    }

    public AnalysisType getAnalysisType() {
        return analysisType;
    }

    public String getPrTitle() {
        return prTitle;
    }

    public String getPrDescription() {
        return prDescription;
    }

    public List<String> getChangedFiles() {
        return changedFiles;
    }

    public List<String> getDiffSnippets() {
        return diffSnippets;
    }

    public String getProjectWorkspace() {
        return projectWorkspace;
    }

    public String getProjectNamespace() {
        return projectNamespace;
    }

    public String getTargetBranchName() {
        return targetBranchName;
    }

    public String getVcsProvider() {
        return vcsProvider;
    }

    public String getRawDiff() {
        return rawDiff;
    }

    public AnalysisMode getAnalysisMode() {
        return analysisMode;
    }

    public String getDeltaDiff() {
        return deltaDiff;
    }

    public String getPreviousCommitHash() {
        return previousCommitHash;
    }

    public String getCurrentCommitHash() {
        return currentCommitHash;
    }

    public PrEnrichmentDataDto getEnrichmentData() {
        return enrichmentData;
    }

    public static Builder<?> builder() {
        return new Builder<>();
    }

    @SuppressWarnings("unchecked")
    public static class Builder<T extends Builder<T>> {
        private Long projectId;
        private String projectNamespace;
        private String projectWorkspace;
        private String projectVcsWorkspace;
        private String projectVcsRepoSlug;
        private AIProviderKey aiProvider;
        private String aiModel;
        private String aiApiKey;
        private Long pullRequestId;
        private String oAuthClient;
        private String oAuthSecret;
        private String accessToken;
        private int maxAllowedTokens;
        private List<AiRequestPreviousIssueDTO> previousCodeAnalysisIssues;
        private boolean useLocalMcp;
        private boolean useMcpTools;
        private AnalysisType analysisType;
        private String prTitle;
        private String prDescription;
        private List<String> changedFiles;
        private List<String> diffSnippets;
        private String targetBranchName;
        private String vcsProvider;
        private String rawDiff;
        // Incremental analysis fields
        private AnalysisMode analysisMode;
        private String deltaDiff;
        private String previousCommitHash;
        private String currentCommitHash;
        // File enrichment data
        private PrEnrichmentDataDto enrichmentData;

        protected Builder() {
        }

        protected T self() {
            return (T) this;
        }

        public T withProjectId(Long projectId) {
            this.projectId = projectId;
            return self();
        }

        public T withPullRequestId(Long pullRequestId) {
            this.pullRequestId = pullRequestId;
            return self();
        }

        public T withProjectAiConnection(AIConnection projectAiConnection) {
            this.aiProvider = projectAiConnection.getProviderKey();
            this.aiModel = projectAiConnection.getAiModel();
            return self();
        }

        public T withProjectVcsConnectionBinding(ProjectVcsConnectionBinding projectVcsConnectionBinding) {
            this.projectVcsRepoSlug = projectVcsConnectionBinding.getRepoSlug();
            this.projectVcsWorkspace = projectVcsConnectionBinding.getWorkspace();
            return self();
        }

        public T withProjectVcsConnectionBindingInfo(String workspace, String repoSlug) {
            this.projectVcsWorkspace = workspace;
            this.projectVcsRepoSlug = repoSlug;
            return self();
        }

        public T withProjectAiConnectionTokenDecrypted(String decryptedToken) {
            this.aiApiKey = decryptedToken;
            return self();
        }

        public T withProjectVcsConnectionCredentials(String oAuthClient, String oAuthSecret) {
            this.oAuthClient = oAuthClient;
            this.oAuthSecret = oAuthSecret;
            return self();
        }

        public T withAccessToken(String accessToken) {
            this.accessToken = accessToken;
            return self();
        }

        public T withPreviousAnalysisData(Optional<CodeAnalysis> optionalPreviousAnalysis) {
            optionalPreviousAnalysis
                    .ifPresent(codeAnalysis -> this.previousCodeAnalysisIssues = codeAnalysis.getIssues()
                            .stream()
                            .map(AiRequestPreviousIssueDTO::fromEntity)
                            .toList());
            return self();
        }

        /**
         * Set previous issues from ALL PR analysis versions.
         * This provides the LLM with complete issue history including resolved issues,
         * helping it understand what was already found and fixed.
         * 
         * Issues are deduplicated by fingerprint (file + line ±3 + severity + truncated
         * reason).
         * When duplicates exist across versions, we keep the most recent version's data
         * but preserve resolved status if ANY version marked it resolved.
         * 
         * @param allPrAnalyses List of all analyses for this PR, ordered by version
         *                      DESC (newest first)
         */
        public T withAllPrAnalysesData(List<CodeAnalysis> allPrAnalyses) {
            if (allPrAnalyses == null || allPrAnalyses.isEmpty()) {
                return self();
            }

            // Convert all issues to DTOs
            List<AiRequestPreviousIssueDTO> allIssues = allPrAnalyses.stream()
                    .flatMap(analysis -> analysis.getIssues().stream())
                    .map(AiRequestPreviousIssueDTO::fromEntity)
                    .toList();

            // Deduplicate: group by fingerprint, keep most recent version but preserve
            // resolved status
            java.util.Map<String, AiRequestPreviousIssueDTO> deduped = new java.util.LinkedHashMap<>();

            for (AiRequestPreviousIssueDTO issue : allIssues) {
                String fingerprint = computeIssueFingerprint(issue);
                AiRequestPreviousIssueDTO existing = deduped.get(fingerprint);

                if (existing == null) {
                    // First occurrence of this issue
                    deduped.put(fingerprint, issue);
                } else {
                    // Duplicate found - keep the one with higher prVersion (more recent)
                    // But if older version is resolved and newer is not, preserve resolved status
                    int existingVersion = existing.prVersion() != null ? existing.prVersion() : 0;
                    int currentVersion = issue.prVersion() != null ? issue.prVersion() : 0;

                    boolean existingResolved = "resolved".equalsIgnoreCase(existing.status());
                    boolean currentResolved = "resolved".equalsIgnoreCase(issue.status());

                    if (currentVersion > existingVersion) {
                        // Current is newer - use it, but preserve resolved status if existing was
                        // resolved
                        if (existingResolved && !currentResolved) {
                            // Older version was resolved but newer one isn't marked - use resolved data
                            // from older
                            deduped.put(fingerprint, mergeResolvedStatus(issue, existing));
                        } else {
                            deduped.put(fingerprint, issue);
                        }
                    } else if (existingVersion == currentVersion) {
                        // Same version - prefer resolved one
                        if (currentResolved && !existingResolved) {
                            deduped.put(fingerprint, issue);
                        }
                    }
                    // If existing is newer, keep it (already in map)
                }
            }

            this.previousCodeAnalysisIssues = new java.util.ArrayList<>(deduped.values());

            return self();
        }

        /**
         * Compute a fingerprint for an issue to detect duplicates across PR versions.
         * Uses: file + normalized line (±3 tolerance) + severity + first 50 chars of
         * reason.
         */
        private String computeIssueFingerprint(AiRequestPreviousIssueDTO issue) {
            String file = issue.file() != null ? issue.file() : "";
            // Normalize line to nearest multiple of 3 for tolerance
            int lineGroup = issue.line() != null ? (issue.line() / 3) : 0;
            String severity = issue.severity() != null ? issue.severity() : "";
            String reasonPrefix = issue.reason() != null
                    ? issue.reason().substring(0, Math.min(50, issue.reason().length())).toLowerCase().trim()
                    : "";

            return file + "::" + lineGroup + "::" + severity + "::" + reasonPrefix;
        }

        /**
         * Merge resolved status from an older issue version into a newer one.
         * Creates a new DTO with the newer issue's data but the older issue's
         * resolution info.
         */
        private AiRequestPreviousIssueDTO mergeResolvedStatus(
                AiRequestPreviousIssueDTO newer,
                AiRequestPreviousIssueDTO resolvedOlder) {
            return new AiRequestPreviousIssueDTO(
                    newer.id(),
                    newer.type(),
                    newer.severity(),
                    newer.reason(),
                    newer.suggestedFixDescription(),
                    newer.suggestedFixDiff(),
                    newer.file(),
                    newer.line(),
                    newer.branch(),
                    newer.pullRequestId(),
                    resolvedOlder.status(), // Use resolved status from older
                    newer.category(),
                    newer.prVersion(),
                    resolvedOlder.resolvedDescription(),
                    resolvedOlder.resolvedByCommit(),
                    resolvedOlder.resolvedInAnalysisId());
        }

        public T withMaxAllowedTokens(int maxAllowedTokens) {
            this.maxAllowedTokens = maxAllowedTokens;
            return self();
        }

        public T withUseLocalMcp(boolean useLocalMcp) {
            this.useLocalMcp = useLocalMcp;
            return self();
        }

        public T withUseMcpTools(boolean useMcpTools) {
            this.useMcpTools = useMcpTools;
            return self();
        }

        public T withAnalysisType(AnalysisType analysisType) {
            this.analysisType = analysisType;
            return self();
        }

        public T withPrTitle(String prTitle) {
            this.prTitle = prTitle;
            return self();
        }

        public T withPrDescription(String prDescription) {
            this.prDescription = prDescription;
            return self();
        }

        public T withChangedFiles(List<String> changedFiles) {
            this.changedFiles = changedFiles;
            return self();
        }

        public T withDiffSnippets(List<String> diffSnippets) {
            this.diffSnippets = diffSnippets;
            return self();
        }

        public T withProjectMetadata(String workspace, String namespace) {
            this.projectWorkspace = workspace;
            this.projectNamespace = namespace;
            return self();
        }

        public T withTargetBranchName(String targetBranchName) {
            this.targetBranchName = targetBranchName;
            return self();
        }

        public T withVcsProvider(String vcsProvider) {
            this.vcsProvider = vcsProvider;
            return self();
        }

        public T withRawDiff(String rawDiff) {
            this.rawDiff = rawDiff;
            return self();
        }

        public T withAnalysisMode(AnalysisMode analysisMode) {
            this.analysisMode = analysisMode;
            return self();
        }

        public T withDeltaDiff(String deltaDiff) {
            this.deltaDiff = deltaDiff;
            return self();
        }

        public T withPreviousCommitHash(String previousCommitHash) {
            this.previousCommitHash = previousCommitHash;
            return self();
        }

        public T withCurrentCommitHash(String currentCommitHash) {
            this.currentCommitHash = currentCommitHash;
            return self();
        }

        public T withEnrichmentData(PrEnrichmentDataDto enrichmentData) {
            this.enrichmentData = enrichmentData;
            return self();
        }

        public AiAnalysisRequestImpl build() {
            return new AiAnalysisRequestImpl(this);
        }
    }

    public boolean getUseLocalMcp() {
        return useLocalMcp;
    }

    @Override
    public boolean getUseMcpTools() {
        return useMcpTools;
    }
}
