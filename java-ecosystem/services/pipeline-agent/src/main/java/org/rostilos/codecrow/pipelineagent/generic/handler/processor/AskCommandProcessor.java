package org.rostilos.codecrow.pipelineagent.generic.handler.processor;

import org.rostilos.codecrow.analysisengine.client.AiCommandClient;
import org.rostilos.codecrow.analysisengine.client.AiCommandClient.AskRequest;
import org.rostilos.codecrow.analysisengine.client.AiCommandClient.AskResult;
import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.ProjectVcsConnectionBinding;
import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
import org.rostilos.codecrow.core.model.vcs.config.cloud.BitbucketCloudConfig;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.pipelineagent.generic.handler.CommentCommandWebhookHandler.CommentCommandProcessor;
import org.rostilos.codecrow.pipelineagent.generic.service.PromptSanitizationService;
import org.rostilos.codecrow.pipelineagent.generic.webhook.WebhookPayload;
import org.rostilos.codecrow.pipelineagent.generic.webhook.handler.WebhookHandler.WebhookResult;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.security.GeneralSecurityException;
import java.util.*;
import java.util.function.Consumer;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Processor for /codecrow ask command.
 * <p>
 * Answers questions about the code, analysis results, or specific issues.
 * Uses MCP tools to access analysis data when available.
 * 
 * Supported question patterns:
 * - "/codecrow ask what is issue #123?" - Get details about a specific issue
 * - "/codecrow ask what changed in this PR?" - Get PR change summary
 * - "/codecrow ask how does the authentication work?" - RAG-based question about codebase
 */
@Component("askCommandProcessor")
public class AskCommandProcessor implements CommentCommandProcessor {
    
    private static final Logger log = LoggerFactory.getLogger(AskCommandProcessor.class);
    
    /** Maximum response length for VCS comment limits */
    private static final int MAX_RESPONSE_LENGTH = 65000;
    
    /** Pattern for issue references in questions */
    private static final Pattern ISSUE_REF_PATTERN = Pattern.compile("#(\\d+)|issue[\\s#]*(\\d+)", Pattern.CASE_INSENSITIVE);
    
    private final CodeAnalysisService codeAnalysisService;
    private final PromptSanitizationService sanitizationService;
    private final AiCommandClient aiCommandClient;
    private final TokenEncryptionService tokenEncryptionService;
    
    public AskCommandProcessor(
            CodeAnalysisService codeAnalysisService,
            PromptSanitizationService sanitizationService,
            AiCommandClient aiCommandClient,
            TokenEncryptionService tokenEncryptionService
    ) {
        this.codeAnalysisService = codeAnalysisService;
        this.sanitizationService = sanitizationService;
        this.aiCommandClient = aiCommandClient;
        this.tokenEncryptionService = tokenEncryptionService;
    }
    
    @Override
    public WebhookResult process(
            WebhookPayload payload,
            Project project,
            Consumer<Map<String, Object>> eventConsumer
    ) {
        return process(payload, project, eventConsumer, Collections.emptyMap());
    }
    
    @Override
    public WebhookResult process(
            WebhookPayload payload,
            Project project,
            Consumer<Map<String, Object>> eventConsumer,
            Map<String, Object> additionalData
    ) {
        // Get the question from additional data or command arguments
        String question = (String) additionalData.getOrDefault("question", "");
        if (question == null || question.isBlank()) {
            // Try to get from comment if not in additional data
            if (payload.commentData() != null && payload.commentData().commentBody() != null) {
                var command = payload.commentData().parseCommand();
                if (command != null && command.arguments() != null) {
                    question = command.arguments();
                }
            }
        }
        
        if (question == null || question.isBlank()) {
            return WebhookResult.error("No question provided. Usage: /codecrow ask <your question>");
        }
        
        log.info("Processing ask command for project={}, PR={}, question length={}", 
            project.getId(), payload.pullRequestId(), question.length());
        
        try {
            // Sanitize the question first
            var sanitizationResult = sanitizationService.sanitize(question);
            if (!sanitizationResult.safe()) {
                eventConsumer.accept(Map.of(
                    "type", "error",
                    "message", "Question blocked: " + sanitizationResult.reason()
                ));
                return WebhookResult.error("Question blocked for security reasons");
            }
            
            String sanitizedQuestion = sanitizationResult.sanitizedInput();
            
            eventConsumer.accept(Map.of(
                "type", "status",
                "state", "analyzing_question",
                "message", "Analyzing your question..."
            ));
            
            // Determine question type and gather context
            QuestionContext context = analyzeQuestion(sanitizedQuestion, project, payload);
            
            eventConsumer.accept(Map.of(
                "type", "status",
                "state", "gathering_context",
                "message", "Gathering relevant context..."
            ));
            
            // Fetch relevant data based on question type
            ContextData contextData = fetchContextData(context, project, payload);
            
            eventConsumer.accept(Map.of(
                "type", "status",
                "state", "generating_answer",
                "message", "Generating answer..."
            ));
            
            // Generate answer
            String answer = generateAnswer(sanitizedQuestion, context, contextData, project, payload);
            
            // Format response
            String formattedResponse = formatResponse(answer, context);
            
            return WebhookResult.success("Answer generated successfully", Map.of(
                "content", formattedResponse,
                "commandType", "ask",
                "questionType", context.questionType().name()
            ));
            
        } catch (Exception e) {
            log.error("Error processing ask command: {}", e.getMessage(), e);
            return WebhookResult.error("Failed to generate answer: " + e.getMessage());
        }
    }
    
    /**
     * Analyze the question to determine its type and extract relevant references.
     */
    private QuestionContext analyzeQuestion(String question, Project project, WebhookPayload payload) {
        QuestionType type = QuestionType.GENERAL;
        List<String> issueReferences = new ArrayList<>();
        boolean aboutCurrentPr = false;
        boolean aboutAnalysis = false;
        
        String lowerQuestion = question.toLowerCase();
        
        // Check for issue references
        Matcher matcher = ISSUE_REF_PATTERN.matcher(question);
        while (matcher.find()) {
            String ref = matcher.group(1) != null ? matcher.group(1) : matcher.group(2);
            if (ref != null) {
                issueReferences.add(ref);
            }
        }
        
        if (!issueReferences.isEmpty()) {
            type = QuestionType.ISSUE_SPECIFIC;
        }
        
        // Check if asking about the current PR
        if (lowerQuestion.contains("this pr") || 
            lowerQuestion.contains("this pull request") ||
            lowerQuestion.contains("current pr") ||
            lowerQuestion.contains("changed")) {
            aboutCurrentPr = true;
            if (type == QuestionType.GENERAL) {
                type = QuestionType.PR_RELATED;
            }
        }
        
        // Check if asking about analysis results
        if (lowerQuestion.contains("analysis") ||
            lowerQuestion.contains("review") ||
            lowerQuestion.contains("issue") ||
            lowerQuestion.contains("problem") ||
            lowerQuestion.contains("finding")) {
            aboutAnalysis = true;
            if (type == QuestionType.GENERAL && !issueReferences.isEmpty()) {
                type = QuestionType.ANALYSIS_RELATED;
            }
        }
        
        // Check if it's a codebase question (likely needs RAG)
        if (lowerQuestion.contains("how does") ||
            lowerQuestion.contains("how is") ||
            lowerQuestion.contains("where is") ||
            lowerQuestion.contains("what is the") ||
            lowerQuestion.contains("explain")) {
            if (type == QuestionType.GENERAL) {
                type = QuestionType.CODEBASE_QUESTION;
            }
        }
        
        return new QuestionContext(
            type,
            issueReferences,
            aboutCurrentPr,
            aboutAnalysis
        );
    }
    
    /**
     * Fetch relevant data based on question context.
     */
    private ContextData fetchContextData(QuestionContext context, Project project, WebhookPayload payload) {
        StringBuilder analysisInfo = new StringBuilder();
        StringBuilder issueInfo = new StringBuilder();
        String ragContext = null;
        
        // Fetch issue details if issue references found
        if (!context.issueReferences().isEmpty()) {
            // TODO: Implement issue detail fetching via CodeCrow Platform MCP server
            // For now, return placeholder
            for (String issueRef : context.issueReferences()) {
                issueInfo.append("Issue #").append(issueRef).append(": Details pending MCP implementation\n");
            }
        }
        
        // Fetch analysis results if about analysis or current PR
        if (context.aboutAnalysis() || context.aboutCurrentPr()) {
            if (payload.pullRequestId() != null && payload.commitHash() != null) {
                Optional<CodeAnalysis> analysis = codeAnalysisService.getCodeAnalysisCache(
                    project.getId(),
                    payload.commitHash(),
                    Long.parseLong(payload.pullRequestId())
                );
                
                if (analysis.isPresent()) {
                    CodeAnalysis ca = analysis.get();
                    analysisInfo.append("Analysis found for commit ").append(payload.commitHash()).append("\n");
                    analysisInfo.append("Total issues: ").append(ca.getTotalIssues()).append("\n");
                    // Add more analysis details as needed
                }
            }
        }
        
        // Fetch RAG context for codebase questions
        if (context.questionType() == QuestionType.CODEBASE_QUESTION) {
            // RAG context will be fetched by the MCP client
            ragContext = null;
        }
        
        return new ContextData(
            analysisInfo.toString(),
            issueInfo.toString(),
            ragContext
        );
    }
    
    /**
     * Generate answer based on question and context.
     */
    private String generateAnswer(
            String question,
            QuestionContext context,
            ContextData contextData,
            Project project,
            WebhookPayload payload
    ) {
        // Try to use AI service
        try {
            AskRequest request = buildAskRequest(project, payload, question, context, contextData);
            
            if (request == null) {
                log.warn("Failed to build ask request - missing AI or VCS configuration");
                return generatePlaceholderAnswer(question, context, contextData);
            }
            
            log.info("Calling AI service to answer question...");
            
            AskResult result = aiCommandClient.ask(request, event -> {
                log.debug("AI ask event: {}", event);
            });
            
            log.info("AI answer generated successfully");
            return result.answer();
            
        } catch (IOException e) {
            log.error("Failed to generate answer via AI: {}", e.getMessage(), e);
            return generatePlaceholderAnswer(question, context, contextData);
        } catch (Exception e) {
            log.error("Unexpected error generating answer: {}", e.getMessage(), e);
            return generatePlaceholderAnswer(question, context, contextData);
        }
    }
    
    /**
     * Build the request for the AI ask endpoint.
     */
    private AskRequest buildAskRequest(
            Project project,
            WebhookPayload payload,
            String question,
            QuestionContext context,
            ContextData contextData
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
            } else if (vcsConnection.getConnectionType() == EVcsConnectionType.APP) {
                accessToken = tokenEncryptionService.decrypt(vcsConnection.getAccessToken());
            }
            
            // Determine VCS provider string
            String vcsProvider = vcsConnection.getProviderType() == EVcsProvider.GITHUB 
                ? "github" 
                : "bitbucket_cloud";
            
            // Build analysis context string
            String analysisContext = contextData.analysisInfo();
            if (!contextData.issueInfo().isBlank()) {
                analysisContext += "\n\n" + contextData.issueInfo();
            }
            
            Long prId = payload.pullRequestId() != null 
                ? Long.parseLong(payload.pullRequestId()) 
                : null;
            
            return new AskRequest(
                project.getId(),
                vcsInfo.workspace(),
                vcsInfo.repoSlug(),
                project.getWorkspace() != null ? project.getWorkspace().getName() : "",
                project.getNamespace() != null ? project.getNamespace() : "",
                aiConnection.getProviderKey().name(),
                aiConnection.getAiModel(),
                decryptedApiKey,
                question,
                prId,
                payload.commitHash(),
                oAuthClient,
                oAuthSecret,
                accessToken,
                aiConnection.getTokenLimitation(),
                vcsProvider,
                analysisContext,
                context.issueReferences()
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
    
    private String buildAnswerPrompt(
            String question,
            QuestionContext context,
            ContextData contextData
    ) {
        StringBuilder prompt = new StringBuilder();
        prompt.append("You are a code review assistant. Answer the following question about a codebase.\n\n");
        
        prompt.append("## Question\n");
        prompt.append(question).append("\n\n");
        
        prompt.append("## Question Type: ").append(context.questionType()).append("\n\n");
        
        if (!contextData.analysisInfo().isBlank()) {
            prompt.append("## Analysis Information\n");
            prompt.append(contextData.analysisInfo()).append("\n\n");
        }
        
        if (!contextData.issueInfo().isBlank()) {
            prompt.append("## Issue Details\n");
            prompt.append(contextData.issueInfo()).append("\n\n");
        }
        
        if (contextData.ragContext() != null && !contextData.ragContext().isBlank()) {
            prompt.append("## Codebase Context (from RAG)\n");
            prompt.append(contextData.ragContext()).append("\n\n");
        }
        
        prompt.append("## Instructions\n");
        prompt.append("Provide a clear, concise answer. ");
        prompt.append("If you reference specific files or code, use proper formatting. ");
        prompt.append("If you don't have enough information to answer, say so clearly.\n");
        
        return prompt.toString();
    }
    
    private String generatePlaceholderAnswer(
            String question,
            QuestionContext context,
            ContextData contextData
    ) {
        StringBuilder answer = new StringBuilder();
        
        switch (context.questionType()) {
            case ISSUE_SPECIFIC -> {
                answer.append("**Issue Information**\n\n");
                if (!contextData.issueInfo().isBlank()) {
                    answer.append(contextData.issueInfo());
                } else {
                    answer.append("Issue details are not yet available. ");
                    answer.append("The CodeCrow Platform MCP server is being implemented to provide issue data.\n");
                }
            }
            case PR_RELATED -> {
                answer.append("**PR Information**\n\n");
                if (!contextData.analysisInfo().isBlank()) {
                    answer.append(contextData.analysisInfo());
                } else {
                    answer.append("No analysis data found for this PR yet.\n");
                }
            }
            case ANALYSIS_RELATED -> {
                answer.append("**Analysis Results**\n\n");
                if (!contextData.analysisInfo().isBlank()) {
                    answer.append(contextData.analysisInfo());
                } else {
                    answer.append("Analysis results are not available. ");
                    answer.append("Run `/codecrow analyze` first to generate analysis.\n");
                }
            }
            case CODEBASE_QUESTION -> {
                answer.append("**Codebase Information**\n\n");
                if (contextData.ragContext() != null && !contextData.ragContext().isBlank()) {
                    answer.append(contextData.ragContext());
                } else {
                    answer.append("RAG context is not available for this project. ");
                    answer.append("Enable RAG indexing in project settings to get codebase-aware answers.\n");
                }
            }
            default -> {
                answer.append("**Answer**\n\n");
                answer.append("_Full AI-powered answers are pending implementation._\n\n");
                answer.append("Your question: \"").append(truncate(question, 200)).append("\"\n\n");
                answer.append("For now, you can:\n");
                answer.append("- Use `/codecrow analyze` to run PR analysis\n");
                answer.append("- Use `/codecrow summarize` to get a PR summary\n");
                answer.append("- Ask about specific issues using `#issue-number`\n");
            }
        }
        
        return answer.toString();
    }
    
    private String formatResponse(String answer, QuestionContext context) {
        StringBuilder sb = new StringBuilder();
        sb.append("<!-- codecrow-ask-response -->\n\n");
        sb.append("## 💬 CodeCrow Answer\n\n");
        sb.append(answer);
        sb.append("\n\n---\n");
        sb.append("_Answered by CodeCrow_ 🦅");
        
        String content = sb.toString();
        if (content.length() > MAX_RESPONSE_LENGTH) {
            content = content.substring(0, MAX_RESPONSE_LENGTH - 50) + "\n\n... (truncated)";
        }
        
        return content;
    }
    
    private String truncate(String text, int maxLength) {
        if (text == null) return "";
        if (text.length() <= maxLength) return text;
        return text.substring(0, maxLength) + "...";
    }
    
    /**
     * Types of questions that can be asked.
     */
    public enum QuestionType {
        GENERAL,           // Generic question
        ISSUE_SPECIFIC,    // Question about specific issue(s)
        PR_RELATED,        // Question about the current PR
        ANALYSIS_RELATED,  // Question about analysis results
        CODEBASE_QUESTION  // Question about the codebase (needs RAG)
    }
    
    /**
     * Context extracted from analyzing a question.
     */
    public record QuestionContext(
        QuestionType questionType,
        List<String> issueReferences,
        boolean aboutCurrentPr,
        boolean aboutAnalysis
    ) {}
    
    /**
     * Data fetched based on question context.
     */
    public record ContextData(
        String analysisInfo,
        String issueInfo,
        String ragContext
    ) {}
}
