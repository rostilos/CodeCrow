package org.rostilos.codecrow.pipelineagent.generic.handler.processor;

import org.rostilos.codecrow.analysisengine.client.AiCommandClient;
import org.rostilos.codecrow.analysisengine.client.AiCommandClient.SummarizeRequest;
import org.rostilos.codecrow.analysisengine.client.AiCommandClient.SummarizeResult;
import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.codeanalysis.PrSummarizeCache;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.ProjectVcsConnectionBinding;
import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
import org.rostilos.codecrow.core.model.vcs.config.cloud.BitbucketCloudConfig;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.PrSummarizeCacheRepository;
import org.rostilos.codecrow.pipelineagent.generic.handler.CommentCommandWebhookHandler.CommentCommandProcessor;
import org.rostilos.codecrow.pipelineagent.generic.webhook.WebhookPayload;
import org.rostilos.codecrow.pipelineagent.generic.webhook.handler.WebhookHandler.WebhookResult;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsReportingService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.security.GeneralSecurityException;
import java.time.OffsetDateTime;
import java.util.Map;
import java.util.function.Consumer;

/**
 * Processor for /codecrow summarize command.
 * <p>
 * Generates a comprehensive summary of the PR including:
 * - High-level description of changes
 * - Key files modified
 * - Architecture diagrams (Mermaid for GitHub, ASCII for Bitbucket)
 * - RAG context from codebase knowledge
 * - Recommendations and impact analysis
 */
@Component("summarizeCommandProcessor")
public class SummarizeCommandProcessor implements CommentCommandProcessor {
    
    private static final Logger log = LoggerFactory.getLogger(SummarizeCommandProcessor.class);
    
    /** Maximum summary length to avoid hitting VCS comment limits */
    private static final int MAX_SUMMARY_LENGTH = 65000;
    
    /** Cache TTL in hours */
    private static final int CACHE_TTL_HOURS = 24;
    
    private final VcsServiceFactory vcsServiceFactory;
    private final PrSummarizeCacheRepository summarizeCacheRepository;
    private final AiCommandClient aiCommandClient;
    private final TokenEncryptionService tokenEncryptionService;
    
    public SummarizeCommandProcessor(
            VcsServiceFactory vcsServiceFactory,
            PrSummarizeCacheRepository summarizeCacheRepository,
            AiCommandClient aiCommandClient,
            TokenEncryptionService tokenEncryptionService
    ) {
        this.vcsServiceFactory = vcsServiceFactory;
        this.summarizeCacheRepository = summarizeCacheRepository;
        this.aiCommandClient = aiCommandClient;
        this.tokenEncryptionService = tokenEncryptionService;
    }
    
    @Override
    public WebhookResult process(
            WebhookPayload payload,
            Project project,
            Consumer<Map<String, Object>> eventConsumer
    ) {
        log.info("Processing summarize command for project={}, PR={}", 
            project.getId(), payload.pullRequestId());
        
        try {
            EVcsProvider provider = getVcsProvider(project);
            VcsReportingService reportingService = vcsServiceFactory.getReportingService(provider);
            
            // Check if provider supports Mermaid diagrams
            boolean supportsMermaid = reportingService.supportsMermaidDiagrams();
            PrSummarizeCache.DiagramType diagramType = supportsMermaid ? 
                PrSummarizeCache.DiagramType.MERMAID : PrSummarizeCache.DiagramType.ASCII;
            
            eventConsumer.accept(Map.of(
                "type", "status",
                "state", "fetching_context",
                "message", "Fetching PR context..."
            ));
            
            // TODO: Implement PR diff fetching via VcsAiClientService
            // For now, we'll work with the basic payload info
            String diff = null; // Will be populated when fetchPrDiff is implemented
            
            eventConsumer.accept(Map.of(
                "type", "status",
                "state", "fetching_rag_context",
                "message", "Retrieving codebase context..."
            ));
            
            // Fetch RAG context if available
            String ragContext = fetchRagContext(project, payload);
            
            eventConsumer.accept(Map.of(
                "type", "status",
                "state", "generating_summary",
                "message", "Generating summary with AI..."
            ));
            
            // Generate summary with AI
            SummaryResult summaryResult = generateSummary(
                project, 
                payload, 
                diff, 
                ragContext, 
                diagramType
            );
            
            if (summaryResult == null) {
                return WebhookResult.error("Failed to generate summary");
            }
            
            eventConsumer.accept(Map.of(
                "type", "status",
                "state", "caching_result",
                "message", "Caching summary..."
            ));
            
            // Cache the result
            PrSummarizeCache cache = cacheResult(
                project, 
                payload, 
                summaryResult
            );
            
            eventConsumer.accept(Map.of(
                "type", "status",
                "state", "posting_summary",
                "message", "Posting summary to PR..."
            ));
            
            // Post summary as comment
            String formattedContent = formatSummaryForPosting(summaryResult, diagramType);
            
            return WebhookResult.success("Summary generated successfully", Map.of(
                "summaryId", cache.getId(),
                "content", formattedContent,
                "commandType", "summarize",
                "diagramType", diagramType.name(),
                "cached", false
            ));
            
        } catch (Exception e) {
            log.error("Error processing summarize command: {}", e.getMessage(), e);
            return WebhookResult.error("Failed to generate summary: " + e.getMessage());
        }
    }
    
    private EVcsProvider getVcsProvider(Project project) {
        if (project.getVcsBinding() != null && project.getVcsBinding().getVcsConnection() != null) {
            return project.getVcsBinding().getVcsConnection().getProviderType();
        }
        if (project.getVcsRepoBinding() != null && project.getVcsRepoBinding().getVcsConnection() != null) {
            return project.getVcsRepoBinding().getVcsConnection().getProviderType();
        }
        throw new IllegalStateException("No VCS connection configured for project: " + project.getId());
    }
    
    private String fetchRagContext(
            Project project, 
            WebhookPayload payload
    ) {
        try {
            // Check if RAG is enabled for this project
            var config = project.getConfiguration();
            if (config == null || config.ragConfig() == null || !config.ragConfig().enabled()) {
                log.debug("RAG not enabled for project {}", project.getId());
                return null;
            }
            
            // RAG context is fetched by the MCP client during summarization
            return null;
            
        } catch (Exception e) {
            log.warn("Failed to fetch RAG context: {}", e.getMessage());
            return null;
        }
    }
    
    private SummaryResult generateSummary(
            Project project,
            WebhookPayload payload,
            String diff,
            String ragContext,
            PrSummarizeCache.DiagramType diagramType
    ) {
        try {
            // Build the request for the AI command client
            SummarizeRequest request = buildSummarizeRequest(project, payload, diagramType);
            
            if (request == null) {
                log.error("Failed to build summarize request - missing AI or VCS configuration");
                return generateFallbackSummary(payload, diagramType);
            }
            
            log.info("Calling AI service for PR summarization...");
            
            // Call the AI service
            SummarizeResult result = aiCommandClient.summarize(request, event -> {
                log.debug("AI summarize event: {}", event);
            });
            
            log.info("AI summarization completed successfully");
            
            // Convert diagram type from string to enum
            PrSummarizeCache.DiagramType resultDiagramType = 
                "ASCII".equalsIgnoreCase(result.diagramType()) 
                    ? PrSummarizeCache.DiagramType.ASCII 
                    : PrSummarizeCache.DiagramType.MERMAID;
            
            return new SummaryResult(
                result.summary(),
                result.diagram(),
                resultDiagramType
            );
            
        } catch (IOException e) {
            log.error("Failed to generate summary via AI: {}", e.getMessage(), e);
            // Return fallback summary on failure
            return generateFallbackSummary(payload, diagramType);
        } catch (Exception e) {
            log.error("Unexpected error generating summary: {}", e.getMessage(), e);
            return generateFallbackSummary(payload, diagramType);
        }
    }
    
    private SummarizeRequest buildSummarizeRequest(
            Project project,
            WebhookPayload payload,
            PrSummarizeCache.DiagramType diagramType
    ) {
        try {
            // Get VCS info
            VcsInfo vcsInfo = getVcsInfo(project);
            if (vcsInfo == null) {
                log.error("No VCS connection configured for project");
                return null;
            }
            
            // Get AI connection
            if (project.getAiBinding() == null || project.getAiBinding().getAiConnection() == null) {
                log.error("No AI connection configured for project");
                return null;
            }
            
            AIConnection aiConnection = project.getAiBinding().getAiConnection();
            String decryptedApiKey = tokenEncryptionService.decrypt(aiConnection.getApiKeyEncrypted());
            
            // Get VCS credentials
            VcsConnection vcsConnection = vcsInfo.vcsConnection();
            String oAuthClient = null;
            String oAuthSecret = null;
            String accessToken = null;
            
            if (vcsConnection.getConnectionType() == EVcsConnectionType.OAUTH_MANUAL) {
                if (vcsConnection.getProviderType() == EVcsProvider.BITBUCKET_CLOUD) {
                    BitbucketCloudConfig config = (BitbucketCloudConfig) vcsConnection.getConfiguration();
                    oAuthClient = tokenEncryptionService.decrypt(config.oAuthKey());
                    oAuthSecret = tokenEncryptionService.decrypt(config.oAuthToken());
                }
            } else if (vcsConnection.getConnectionType() == EVcsConnectionType.APP || 
                       vcsConnection.getConnectionType() == EVcsConnectionType.FORGE_APP) {
                accessToken = tokenEncryptionService.decrypt(vcsConnection.getAccessToken());
            }
            
            // Determine VCS provider string
            String vcsProvider = vcsConnection.getProviderType() == EVcsProvider.GITHUB 
                ? "github" 
                : "bitbucket_cloud";
            
            return new SummarizeRequest(
                project.getId(),
                vcsInfo.workspace(),
                vcsInfo.repoSlug(),
                project.getWorkspace() != null ? project.getWorkspace().getName() : "",
                project.getNamespace() != null ? project.getNamespace() : "",
                aiConnection.getProviderKey().name(),
                aiConnection.getAiModel(),
                decryptedApiKey,
                Long.parseLong(payload.pullRequestId()),
                payload.sourceBranch(),
                payload.targetBranch(),
                payload.commitHash(),
                oAuthClient,
                oAuthSecret,
                accessToken,
                diagramType == PrSummarizeCache.DiagramType.MERMAID,
                aiConnection.getTokenLimitation(),
                vcsProvider
            );
            
        } catch (GeneralSecurityException e) {
            log.error("Failed to decrypt credentials: {}", e.getMessage());
            return null;
        }
    }
    
    /**
     * Helper record to hold VCS connection info.
     */
    private record VcsInfo(VcsConnection vcsConnection, String workspace, String repoSlug) {}
    
    /**
     * Get VCS info from the project.
     */
    private VcsInfo getVcsInfo(Project project) {
        ProjectVcsConnectionBinding vcsBinding = project.getVcsBinding();
        if (vcsBinding != null && vcsBinding.getVcsConnection() != null) {
            return new VcsInfo(vcsBinding.getVcsConnection(), vcsBinding.getWorkspace(), vcsBinding.getRepoSlug());
        }
        
        VcsRepoBinding repoBinding = project.getVcsRepoBinding();
        if (repoBinding != null && repoBinding.getVcsConnection() != null) {
            return new VcsInfo(repoBinding.getVcsConnection(), repoBinding.getExternalNamespace(), repoBinding.getExternalRepoSlug());
        }
        
        return null;
    }
    
    /**
     * Generate a fallback summary when AI service is unavailable.
     */
    private SummaryResult generateFallbackSummary(WebhookPayload payload, PrSummarizeCache.DiagramType diagramType) {
        String summaryContent = generatePlaceholderSummary(payload, diagramType);
        String diagramContent = generatePlaceholderDiagram(diagramType);
        
        return new SummaryResult(
            summaryContent,
            diagramContent,
            diagramType
        );
    }
    
    private String buildSummaryPrompt(
            WebhookPayload payload,
            String diff,
            String ragContext,
            PrSummarizeCache.DiagramType diagramType
    ) {
        StringBuilder prompt = new StringBuilder();
        prompt.append("You are a code review assistant. Analyze this pull request and provide a comprehensive summary.\n\n");
        
        prompt.append("## Pull Request Information\n");
        prompt.append("Source Branch: ").append(payload.sourceBranch() != null ? payload.sourceBranch() : "N/A").append("\n");
        prompt.append("Target Branch: ").append(payload.targetBranch() != null ? payload.targetBranch() : "N/A").append("\n\n");
        
        if (diff != null && !diff.isBlank()) {
            prompt.append("## Code Changes (Diff)\n");
            prompt.append("```diff\n");
            // Truncate diff if too long
            String truncatedDiff = diff.length() > 50000 ? diff.substring(0, 50000) + "\n... (truncated)" : diff;
            prompt.append(truncatedDiff);
            prompt.append("\n```\n\n");
        }
        
        if (ragContext != null && !ragContext.isBlank()) {
            prompt.append("## Codebase Context (from RAG)\n");
            prompt.append(ragContext).append("\n\n");
        }
        
        prompt.append("## Instructions\n");
        prompt.append("Provide a summary with:\n");
        prompt.append("1. **Overview**: High-level description of what this PR does\n");
        prompt.append("2. **Key Changes**: List the most important files and what changed\n");
        prompt.append("3. **Impact Analysis**: Potential impact on the codebase\n");
        prompt.append("4. **Recommendations**: Any suggestions for improvement\n\n");
        
        if (diagramType == PrSummarizeCache.DiagramType.MERMAID) {
            prompt.append("5. **Architecture Diagram**: Create a Mermaid diagram showing the main components affected\n");
            prompt.append("Use ```mermaid code blocks for diagrams.\n");
        } else {
            prompt.append("5. **Architecture Diagram**: Create an ASCII art diagram showing the main components affected\n");
            prompt.append("Use ``` code blocks for ASCII diagrams.\n");
        }
        
        return prompt.toString();
    }
    
    private String generatePlaceholderSummary(WebhookPayload payload, PrSummarizeCache.DiagramType diagramType) {
        StringBuilder sb = new StringBuilder();
        sb.append("## 📋 PR Summary\n\n");
        
        sb.append("### Overview\n");
        sb.append("This pull request contains changes from branch `")
          .append(payload.sourceBranch() != null ? payload.sourceBranch() : "unknown")
          .append("` to `")
          .append(payload.targetBranch() != null ? payload.targetBranch() : "unknown")
          .append("`.\n\n");
        
        sb.append("### Key Changes\n");
        sb.append("_Summary generation via AI is pending implementation._\n\n");
        
        sb.append("### Impact Analysis\n");
        sb.append("_Analysis pending._\n\n");
        
        sb.append("### Recommendations\n");
        sb.append("_Recommendations pending._\n\n");
        
        return sb.toString();
    }
    
    private String generatePlaceholderDiagram(PrSummarizeCache.DiagramType diagramType) {
        if (diagramType == PrSummarizeCache.DiagramType.MERMAID) {
            return """
                ```mermaid
                graph TD
                    A[Pull Request] --> B[Code Changes]
                    B --> C[Impact Analysis]
                    C --> D[Review]
                    D --> E[Merge]
                ```
                """;
        } else {
            return """
                ```
                +---------------+
                | Pull Request  |
                +-------+-------+
                        |
                        v
                +---------------+
                | Code Changes  |
                +-------+-------+
                        |
                        v
                +---------------+
                |    Review     |
                +-------+-------+
                        |
                        v
                +---------------+
                |    Merge      |
                +---------------+
                ```
                """;
        }
    }
    
    private PrSummarizeCache cacheResult(
            Project project,
            WebhookPayload payload,
            SummaryResult summaryResult
    ) {
        PrSummarizeCache cache = new PrSummarizeCache();
        cache.setProject(project);
        cache.setCommitHash(payload.commitHash());
        cache.setPrNumber(Long.parseLong(payload.pullRequestId()));
        cache.setSummaryContent(summaryResult.summaryContent());
        cache.setDiagramContent(summaryResult.diagramContent());
        cache.setDiagramType(summaryResult.diagramType());
        cache.setExpiresAt(OffsetDateTime.now().plusHours(CACHE_TTL_HOURS));
        
        return summarizeCacheRepository.save(cache);
    }
    
    private String formatSummaryForPosting(SummaryResult result, PrSummarizeCache.DiagramType diagramType) {
        StringBuilder sb = new StringBuilder();
        sb.append("<!-- codecrow-summary -->\n\n");
        sb.append(result.summaryContent());
        
        if (result.diagramContent() != null && !result.diagramContent().isBlank()) {
            sb.append("\n### Architecture Diagram\n\n");
            sb.append(result.diagramContent());
        }
        
        sb.append("\n\n---\n");
        sb.append("_Generated by CodeCrow_ 🦅");
        
        String content = sb.toString();
        if (content.length() > MAX_SUMMARY_LENGTH) {
            content = content.substring(0, MAX_SUMMARY_LENGTH - 50) + "\n\n... (truncated)";
        }
        
        return content;
    }
    
    /**
     * Result of summary generation.
     */
    public record SummaryResult(
        String summaryContent,
        String diagramContent,
        PrSummarizeCache.DiagramType diagramType
    ) {}
}
