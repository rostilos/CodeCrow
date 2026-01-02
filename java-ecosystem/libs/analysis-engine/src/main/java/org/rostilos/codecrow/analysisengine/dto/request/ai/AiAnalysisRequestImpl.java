package org.rostilos.codecrow.analysisengine.dto.request.ai;

import com.fasterxml.jackson.annotation.JsonProperty;
import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.ai.AIProviderKey;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisMode;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.ProjectVcsConnectionBinding;

import java.util.List;
import java.util.Optional;

public class AiAnalysisRequestImpl implements AiAnalysisRequest{
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

    public int getMaxAllowedTokens() { return maxAllowedTokens; }

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
            optionalPreviousAnalysis.ifPresent(codeAnalysis ->
                    this.previousCodeAnalysisIssues = codeAnalysis.getIssues()
                            .stream()
                            .map(AiRequestPreviousIssueDTO::fromEntity)
                            .toList()
            );
            return self();
        }

        public T withMaxAllowedTokens(int maxAllowedTokens) {
            this.maxAllowedTokens = maxAllowedTokens;
            return self();
        }

        public T withUseLocalMcp(boolean useLocalMcp) {
            this.useLocalMcp = useLocalMcp;
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

        public AiAnalysisRequestImpl build() {
            return new AiAnalysisRequestImpl(this);
        }
    }

    public boolean getUseLocalMcp() {
        return useLocalMcp;
    }
}
