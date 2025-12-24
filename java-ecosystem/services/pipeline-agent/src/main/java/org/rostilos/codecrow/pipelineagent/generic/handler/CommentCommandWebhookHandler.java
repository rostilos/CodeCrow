package org.rostilos.codecrow.pipelineagent.generic.handler;

import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.PrSummarizeCache;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.PrSummarizeCacheRepository;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.analysisengine.processor.analysis.PullRequestAnalysisProcessor;
import org.rostilos.codecrow.pipelineagent.generic.service.CommentCommandRateLimitService;
import org.rostilos.codecrow.pipelineagent.generic.service.PromptSanitizationService;
import org.rostilos.codecrow.pipelineagent.generic.webhook.WebhookPayload;
import org.rostilos.codecrow.pipelineagent.generic.webhook.WebhookPayload.CodecrowCommand;
import org.rostilos.codecrow.pipelineagent.generic.webhook.WebhookPayload.CommentData;
import org.rostilos.codecrow.pipelineagent.generic.webhook.handler.WebhookHandler;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.time.OffsetDateTime;
import java.util.Map;
import java.util.Optional;
import java.util.function.Consumer;

/**
 * Generic handler for comment-triggered CodeCrow commands.
 * Supports both Bitbucket Cloud and GitHub comment webhooks.
 * 
 * Commands:
 * - /codecrow analyze - Trigger PR analysis
 * - /codecrow summarize - Generate PR summary with diagrams
 * - /codecrow ask <question> - Ask questions about the code/analysis
 */
@Component
public class CommentCommandWebhookHandler implements WebhookHandler {
    
    private static final Logger log = LoggerFactory.getLogger(CommentCommandWebhookHandler.class);

    
    private final CommentCommandRateLimitService rateLimitService;
    private final PromptSanitizationService sanitizationService;
    private final CodeAnalysisService codeAnalysisService;
    private final PrSummarizeCacheRepository summarizeCacheRepository;
    private final PullRequestAnalysisProcessor pullRequestAnalysisProcessor;
    
    // Command processors injected via Spring
    private final CommentCommandProcessor summarizeProcessor;
    private final CommentCommandProcessor askProcessor;
    
    public CommentCommandWebhookHandler(
            CommentCommandRateLimitService rateLimitService,
            PromptSanitizationService sanitizationService,
            CodeAnalysisService codeAnalysisService,
            PrSummarizeCacheRepository summarizeCacheRepository,
            PullRequestAnalysisProcessor pullRequestAnalysisProcessor,
            @org.springframework.beans.factory.annotation.Qualifier("summarizeCommandProcessor") CommentCommandProcessor summarizeProcessor,
            @org.springframework.beans.factory.annotation.Qualifier("askCommandProcessor") CommentCommandProcessor askProcessor
    ) {
        this.rateLimitService = rateLimitService;
        this.sanitizationService = sanitizationService;
        this.codeAnalysisService = codeAnalysisService;
        this.summarizeCacheRepository = summarizeCacheRepository;
        this.pullRequestAnalysisProcessor = pullRequestAnalysisProcessor;
        this.summarizeProcessor = summarizeProcessor;
        this.askProcessor = askProcessor;
    }
    
    @Override
    public EVcsProvider getProvider() {
        // This handler supports multiple providers
        return null;
    }
    
    /**
     * Check if this handler supports the given event type.
     * Supports comment events from both Bitbucket and GitHub.
     */
    @Override
    public boolean supportsEvent(String eventType) {
        if (eventType == null) return false;
        
        return eventType.contains("comment") || 
               eventType.equals("issue_comment") ||
               eventType.equals("pull_request_review_comment");
    }
    
    /**
     * Check if this handler supports a specific provider's comment events.
     */
    public boolean supportsProviderEvent(EVcsProvider provider, String eventType) {
        if (eventType == null) return false;
        
        return switch (provider) {
            case BITBUCKET_CLOUD -> eventType.startsWith("pullrequest:comment");
            case GITHUB -> eventType.equals("issue_comment") || eventType.equals("pull_request_review_comment");
            default -> false;
        };
    }
    
    @Override
    public WebhookResult handle(WebhookPayload payload, Project project, Consumer<Map<String, Object>> eventConsumer) {
        if (!payload.isCommentEvent() || !payload.hasCodecrowCommand()) {
            return WebhookResult.ignored("Not a CodeCrow command comment");
        }
        
        CodecrowCommand command = payload.getCodecrowCommand();
        
        log.info("Processing CodeCrow command: type={}, project={}, PR={}", 
            command.type(), project.getId(), payload.pullRequestId());
        
        // Validate comment commands are enabled
        ValidationResult validation = validateRequest(project, payload, command);
        if (!validation.isValid()) {
            eventConsumer.accept(Map.of(
                "type", "error",
                "message", validation.message()
            ));
            return WebhookResult.error(validation.message());
        }
        
        // Check rate limit
        CommentCommandRateLimitService.RateLimitCheckResult rateLimitResult = 
            rateLimitService.checkRateLimit(project);
        
        if (!rateLimitResult.allowed()) {
            eventConsumer.accept(Map.of(
                "type", "rate_limited",
                "message", rateLimitResult.message(),
                "remaining", rateLimitResult.remaining(),
                "seconds_until_reset", rateLimitResult.secondsUntilReset()
            ));
            return WebhookResult.error(rateLimitResult.message());
        }
        
        // Record command for rate limiting
        rateLimitService.recordCommand(project);
        
        // Process based on command type
        return switch (command.type()) {
            case ANALYZE -> handleAnalyzeCommand(payload, project, eventConsumer);
            case SUMMARIZE -> handleSummarizeCommand(payload, project, eventConsumer);
            case REVIEW -> handleReviewCommand(payload, project, eventConsumer);
            case ASK -> handleAskCommand(payload, project, command.arguments(), eventConsumer);
        };
    }
    
    /**
     * Handle /codecrow analyze command.
     * Returns cached results if available for the same commit hash.
     */
    private WebhookResult handleAnalyzeCommand(
            WebhookPayload payload, 
            Project project, 
            Consumer<Map<String, Object>> eventConsumer
    ) {
        log.info("Handling analyze command for project={}, PR={}", project.getId(), payload.pullRequestId());
        
        eventConsumer.accept(Map.of(
            "type", "status",
            "state", "checking_cache",
            "message", "Checking for existing analysis..."
        ));
        
        // Check for cached analysis
        if (payload.commitHash() != null && payload.pullRequestId() != null) {
            Optional<CodeAnalysis> cachedAnalysis = codeAnalysisService.getCodeAnalysisCache(
                project.getId(),
                payload.commitHash(),
                Long.parseLong(payload.pullRequestId())
            );
            
            if (cachedAnalysis.isPresent()) {
                eventConsumer.accept(Map.of(
                    "type", "status",
                    "state", "cache_hit",
                    "message", "Found existing analysis, posting results..."
                ));
                
                // Return cached result - the VCS reporting service will post it
                return WebhookResult.success("Analysis retrieved from cache", Map.of(
                    "cached", true,
                    "analysisId", cachedAnalysis.get().getId(),
                    "commandType", "analyze"
                ));
            }
        }
        
        // Run PR analysis using the processor
        return runPrAnalysis(payload, project, eventConsumer, "analyze");
    }
    
    /**
     * Handle /codecrow summarize command.
     * Generates documentation and diagrams for the PR.
     */
    private WebhookResult handleSummarizeCommand(
            WebhookPayload payload, 
            Project project, 
            Consumer<Map<String, Object>> eventConsumer
    ) {
        log.info("Handling summarize command for project={}, PR={}", project.getId(), payload.pullRequestId());
        
        try {
            eventConsumer.accept(Map.of(
                "type", "status",
                "state", "checking_cache",
                "message", "Checking for existing summary..."
            ));
            
            // Check for cached summary
            log.debug("Checking cache: commitHash={}, pullRequestId={}", payload.commitHash(), payload.pullRequestId());
            if (payload.commitHash() != null && payload.pullRequestId() != null) {
                Optional<PrSummarizeCache> cachedSummary = summarizeCacheRepository.findValidCache(
                    project.getId(),
                    payload.commitHash(),
                    Long.parseLong(payload.pullRequestId()),
                    OffsetDateTime.now()
                );
                
                log.debug("Cache check result: present={}", cachedSummary.isPresent());
                
                if (cachedSummary.isPresent()) {
                    eventConsumer.accept(Map.of(
                        "type", "status",
                        "state", "cache_hit",
                        "message", "Found existing summary, posting..."
                    ));
                    
                    return WebhookResult.success("Summary retrieved from cache", Map.of(
                        "cached", true,
                        "summaryId", cachedSummary.get().getId(),
                        "content", cachedSummary.get().getFormattedContent(),
                        "commandType", "summarize"
                    ));
                }
            }
            
            eventConsumer.accept(Map.of(
                "type", "status",
                "state", "generating_summary",
                "message", "Generating summary and diagrams..."
            ));
            
            // Delegate to summarize processor
            log.debug("summarizeProcessor is null: {}", summarizeProcessor == null);
            if (summarizeProcessor != null) {
                log.info("Delegating to summarizeProcessor");
                return summarizeProcessor.process(payload, project, eventConsumer);
            }
            
            log.warn("No summarize processor available, returning queued");
            return WebhookResult.queued("Summary generation queued");
        } catch (Exception e) {
            log.error("Error in handleSummarizeCommand: {}", e.getMessage(), e);
            return WebhookResult.error("Failed to process summarize command: " + e.getMessage());
        }
    }
    
    /**
     * Handle /codecrow ask command.
     * Answers questions about the code or analysis.
     */
    private WebhookResult handleAskCommand(
            WebhookPayload payload, 
            Project project, 
            String question,
            Consumer<Map<String, Object>> eventConsumer
    ) {
        log.info("Handling ask command for project={}, PR={}", project.getId(), payload.pullRequestId());
        
        // Sanitize the question
        PromptSanitizationService.SanitizationResult sanitizationResult = 
            sanitizationService.sanitize(question);
        
        if (!sanitizationResult.safe()) {
            eventConsumer.accept(Map.of(
                "type", "error",
                "message", sanitizationResult.reason()
            ));
            return WebhookResult.error(sanitizationResult.reason());
        }
        
        String sanitizedQuestion = sanitizationResult.sanitizedInput();
        
        eventConsumer.accept(Map.of(
            "type", "status",
            "state", "processing_question",
            "message", "Processing your question..."
        ));
        
        // Extract issue references from question
        var issueRefs = sanitizationService.extractIssueReferences(sanitizedQuestion);
        if (!issueRefs.isEmpty()) {
            eventConsumer.accept(Map.of(
                "type", "status",
                "state", "fetching_issues",
                "message", String.format("Found %d issue reference(s), fetching details...", issueRefs.size())
            ));
        }
        
        // Delegate to ask processor
        if (askProcessor != null) {
            return askProcessor.process(payload, project, eventConsumer, Map.of(
                "question", sanitizedQuestion,
                "issueReferences", issueRefs,
                "commentId", payload.commentData().commentId()
            ));
        }
        
        return WebhookResult.queued("Question processing queued");
    }
    
    /**
     * Handle /codecrow review command.
     * Uses the same PR analysis infrastructure as /codecrow analyze.
     */
    private WebhookResult handleReviewCommand(
            WebhookPayload payload, 
            Project project, 
            Consumer<Map<String, Object>> eventConsumer
    ) {
        log.info("Handling review command for project={}, PR={}", project.getId(), payload.pullRequestId());
        
        // Run PR analysis - same as analyze command
        return runPrAnalysis(payload, project, eventConsumer, "review");
    }
    
    /**
     * Run PR analysis using the PullRequestAnalysisProcessor.
     */
    private WebhookResult runPrAnalysis(
            WebhookPayload payload,
            Project project,
            Consumer<Map<String, Object>> eventConsumer,
            String commandType
    ) {
        try {
            eventConsumer.accept(Map.of(
                "type", "status",
                "state", "starting_analysis",
                "message", "Starting PR analysis..."
            ));
            
            // Convert WebhookPayload to PrProcessRequest
            PrProcessRequest request = new PrProcessRequest();
            request.projectId = project.getId();
            request.pullRequestId = Long.parseLong(payload.pullRequestId());
            request.sourceBranchName = payload.sourceBranch();
            request.targetBranchName = payload.targetBranch();
            request.commitHash = payload.commitHash();
            request.analysisType = AnalysisType.PR_REVIEW;
            
            log.info("Processing PR analysis via {}: project={}, PR={}, source={}, target={}", 
                commandType, project.getId(), request.pullRequestId, request.sourceBranchName, request.targetBranchName);
            
            // Delegate to existing processor
            Map<String, Object> result = pullRequestAnalysisProcessor.process(
                request, 
                eventConsumer::accept, 
                project
            );
            
            log.debug("PR analysis result: {}", result);
            
            boolean cached = Boolean.TRUE.equals(result.get("cached"));
            Object analysisId = result.get("analysisId");
            
            if (cached && analysisId != null) {
                return WebhookResult.success("Analysis result retrieved from cache", Map.of(
                    "cached", true,
                    "analysisId", analysisId,
                    "commandType", commandType
                ));
            }
            
            // Check if analysis was successful - analysisId may be null if result was posted directly
            if (analysisId != null) {
                return WebhookResult.success("Analysis completed successfully", Map.of(
                    "analysisId", analysisId,
                    "commandType", commandType
                ));
            }
            
            // Analysis completed but no analysisId in result - this is OK if results were posted
            if (result.containsKey("status") && "completed".equals(result.get("status"))) {
                return WebhookResult.success("Analysis completed and results posted", Map.of(
                    "commandType", commandType
                ));
            }
            
            // If we got here, the processor posted results directly (which it does)
            return WebhookResult.success("Analysis completed", Map.of("commandType", commandType));
            
        } catch (Exception e) {
            log.error("Error running PR analysis for {} command: {}", commandType, e.getMessage(), e);
            return WebhookResult.error("Analysis failed: " + e.getMessage());
        }
    }
    
    /**
     * Validate the command request.
     */
    private ValidationResult validateRequest(Project project, WebhookPayload payload, CodecrowCommand command) {
        ProjectConfig config = project.getConfiguration();
        
        log.debug("Validating request: config={}, isCommentCommandsEnabled={}", 
            config != null, config != null ? config.isCommentCommandsEnabled() : "N/A");
        
        // Check if comment commands are enabled
        if (config == null || !config.isCommentCommandsEnabled()) {
            log.info("Comment commands not enabled for project {}: config={}, enabled={}", 
                project.getId(), config != null, config != null ? config.isCommentCommandsEnabled() : false);
            return ValidationResult.invalid("Comment commands are not enabled for this project");
        }
        
        // Check if this command type is allowed
        ProjectConfig.CommentCommandsConfig commandsConfig = config.getCommentCommandsConfig();
        if (!commandsConfig.isCommandAllowed(command.getTypeString())) {
            log.info("Command type {} not allowed for project {}", command.getTypeString(), project.getId());
            return ValidationResult.invalid("This command type is not enabled for this project");
        }
        
        // TODO: Check if user is authorized
        // TODO: For now, we check if the comment author is a workspace member
        String authorId = payload.commentData().commentAuthorId();
        if (authorId != null && !isAuthorizedUser(project, authorId, payload)) {
            log.info("User {} not authorized for project {}", authorId, project.getId());
            return ValidationResult.invalid("You are not authorized to use CodeCrow commands on this repository");
        }
        
        // Check PR ID
        if (payload.pullRequestId() == null) {
            log.info("No PR ID in payload for project {}", project.getId());
            return ValidationResult.invalid("Cannot process command: Pull request ID not found");
        }
        
        log.debug("Request validation passed for project {}", project.getId());
        return ValidationResult.valid();
    }
    
    /**
     * Check if the comment author is authorized to use commands.
     */
    private boolean isAuthorizedUser(Project project, String authorId, WebhookPayload payload) {
        // For now, any workspace member can use commands
        // In the future, this could check specific permissions
        
        // TODO: Implement proper user mapping from VCS provider user ID to CodeCrow user
        // For now, allow all commands from workspace with connected repos
        
        return project.getWorkspace() != null;
    }
    
    /**
     * Validation result for command requests.
     */
    private record ValidationResult(boolean isValid, String message) {
        public static ValidationResult valid() {
            return new ValidationResult(true, null);
        }
        
        public static ValidationResult invalid(String message) {
            return new ValidationResult(false, message);
        }
    }
    
    /**
     * Interface for command-specific processors.
     */
    public interface CommentCommandProcessor {
        WebhookResult process(
            WebhookPayload payload, 
            Project project, 
            Consumer<Map<String, Object>> eventConsumer
        );
        
        default WebhookResult process(
            WebhookPayload payload, 
            Project project, 
            Consumer<Map<String, Object>> eventConsumer,
            Map<String, Object> additionalData
        ) {
            return process(payload, project, eventConsumer);
        }
    }
}
