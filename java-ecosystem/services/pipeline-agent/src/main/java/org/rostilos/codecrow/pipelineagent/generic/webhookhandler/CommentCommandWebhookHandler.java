package org.rostilos.codecrow.pipelineagent.generic.webhookhandler;

import com.fasterxml.jackson.databind.JsonNode;
import okhttp3.OkHttpClient;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.PrSummarizeCache;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.CommentCommandsConfig;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.PrSummarizeCacheRepository;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.analysisengine.exception.AnalysisLockedException;
import org.rostilos.codecrow.analysisengine.exception.DiffTooLargeException;
import org.rostilos.codecrow.analysisengine.processor.analysis.PullRequestAnalysisProcessor;
import org.rostilos.codecrow.pipelineagent.generic.dto.webhook.WebhookPayload;
import org.rostilos.codecrow.pipelineagent.generic.service.CommandAuthorizationService;
import org.rostilos.codecrow.pipelineagent.generic.service.CommandAuthorizationService.AuthorizationResult;
import org.rostilos.codecrow.pipelineagent.generic.service.CommentCommandRateLimitService;
import org.rostilos.codecrow.analysisengine.service.PromptSanitizationService;
import org.rostilos.codecrow.pipelineagent.generic.dto.webhook.WebhookPayload.CodecrowCommand;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.rostilos.codecrow.vcsclient.github.actions.GetPullRequestAction;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.io.IOException;
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
    private final VcsClientProvider vcsClientProvider;
    private final CommandAuthorizationService authorizationService;
    
    // Command processors injected via Spring
    private final CommentCommandProcessor summarizeProcessor;
    private final CommentCommandProcessor askProcessor;
    
    public CommentCommandWebhookHandler(
            CommentCommandRateLimitService rateLimitService,
            PromptSanitizationService sanitizationService,
            CodeAnalysisService codeAnalysisService,
            PrSummarizeCacheRepository summarizeCacheRepository,
            PullRequestAnalysisProcessor pullRequestAnalysisProcessor,
            VcsClientProvider vcsClientProvider,
            CommandAuthorizationService authorizationService,
            @org.springframework.beans.factory.annotation.Qualifier("summarizeCommandProcessor") CommentCommandProcessor summarizeProcessor,
            @org.springframework.beans.factory.annotation.Qualifier("askCommandProcessor") CommentCommandProcessor askProcessor
    ) {
        this.rateLimitService = rateLimitService;
        this.sanitizationService = sanitizationService;
        this.codeAnalysisService = codeAnalysisService;
        this.summarizeCacheRepository = summarizeCacheRepository;
        this.pullRequestAnalysisProcessor = pullRequestAnalysisProcessor;
        this.vcsClientProvider = vcsClientProvider;
        this.authorizationService = authorizationService;
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
     * Supports comment events from Bitbucket, GitHub, and GitLab.
     */
    @Override
    public boolean supportsEvent(String eventType) {
        if (eventType == null) return false;
        
        return eventType.contains("comment") || 
               eventType.equals("issue_comment") ||
               eventType.equals("pull_request_review_comment") ||
               eventType.equals("note");  // GitLab Note Hook
    }
    
    /**
     * Check if this handler supports a specific provider's comment events.
     */
    public boolean supportsProviderEvent(EVcsProvider provider, String eventType) {
        if (eventType == null) return false;
        
        return switch (provider) {
            case BITBUCKET_CLOUD -> eventType.startsWith("pullrequest:comment");
            case GITHUB -> eventType.equals("issue_comment") || eventType.equals("pull_request_review_comment");
            case GITLAB -> eventType.equals("note");
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
        
        // Enrich payload with PR details if missing (needed for GitHub issue_comment events)
        WebhookPayload enrichedPayload = enrichPayloadWithPrDetails(payload, project);
        
        // Process based on command type
        return switch (command.type()) {
            case ANALYZE -> handleAnalyzeCommand(enrichedPayload, project, eventConsumer);
            case SUMMARIZE -> handleSummarizeCommand(enrichedPayload, project, eventConsumer);
            case REVIEW -> handleReviewCommand(enrichedPayload, project, eventConsumer);
            case ASK -> handleAskCommand(enrichedPayload, project, command.arguments(), eventConsumer);
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
            request.prAuthorId = payload.prAuthorId();
            request.prAuthorUsername = payload.prAuthorUsername();
            
            // Fetch PR details from API if branch info is missing (e.g., for GitHub issue_comment events)
            if (request.sourceBranchName == null || request.targetBranchName == null) {
                log.info("Branch info missing from webhook payload, fetching PR details from API...");
                try {
                    PrDetails prDetails = fetchPrDetails(project, request.pullRequestId.intValue());
                    if (prDetails != null) {
                        if (request.sourceBranchName == null) {
                            request.sourceBranchName = prDetails.sourceBranch;
                            log.debug("Fetched source branch: {}", prDetails.sourceBranch);
                        }
                        if (request.targetBranchName == null) {
                            request.targetBranchName = prDetails.targetBranch;
                            log.debug("Fetched target branch: {}", prDetails.targetBranch);
                        }
                        if (request.commitHash == null && prDetails.headCommitHash != null) {
                            request.commitHash = prDetails.headCommitHash;
                            log.debug("Fetched commit hash: {}", prDetails.headCommitHash);
                        }
                    }
                } catch (Exception e) {
                    log.warn("Failed to fetch PR details from API: {}", e.getMessage());
                    // Continue with whatever info we have - the processor may handle it
                }
            }
            
            log.info("Processing PR analysis via {}: project={}, PR={}, source={}, target={}", 
                commandType, project.getId(), request.pullRequestId, request.sourceBranchName, request.targetBranchName);
            
            // Delegate to existing processor
            Map<String, Object> result = pullRequestAnalysisProcessor.process(
                request, 
                eventConsumer::accept, 
                project
            );
            
            log.debug("PR analysis result: {}", result);
            
            // Check if analysis failed (processor returns status=error on IOException)
            if ("error".equals(result.get("status"))) {
                String errorMessage = (String) result.getOrDefault("message", "Analysis failed");
                return WebhookResult.error("PR analysis failed: " + errorMessage);
            }
            
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
            
        } catch (DiffTooLargeException e) {
            // Re-throw DiffTooLargeException so WebhookAsyncProcessor can handle it with proper job status
            log.warn("PR diff too large for {} command: {}", commandType, e.getMessage());
            throw e;
        } catch (AnalysisLockedException e) {
            // Re-throw AnalysisLockedException so WebhookAsyncProcessor can handle it with proper job status
            log.warn("Lock acquisition failed for {} command: {}", commandType, e.getMessage());
            throw e;
        } catch (Exception e) {
            log.error("Error running PR analysis for {} command: {}", commandType, e.getMessage(), e);
            return WebhookResult.error("Analysis failed: " + e.getMessage());
        }
    }
    
    /**
     * PR details fetched from VCS API.
     */
    private record PrDetails(String sourceBranch, String targetBranch, String headCommitHash) {}
    
    /**
     * Fetch PR details from the VCS provider API.
     * This is needed when webhook events don't include branch information (e.g., GitHub issue_comment events).
     */
    private PrDetails fetchPrDetails(Project project, int prNumber) throws IOException {
        // Get VCS connection info using unified accessor
        var vcsInfo = project.getEffectiveVcsRepoInfo();
        if (vcsInfo == null || vcsInfo.getVcsConnection() == null) {
            log.warn("Cannot fetch PR details: missing VCS connection info for project {}", project.getId());
            return null;
        }
        
        VcsConnection vcsConnection = vcsInfo.getVcsConnection();
        String owner = vcsInfo.getRepoWorkspace();
        String repoSlug = vcsInfo.getRepoSlug();
        
        if (owner == null || repoSlug == null) {
            log.warn("Cannot fetch PR details: missing VCS connection info for project {}", project.getId());
            return null;
        }
        
        EVcsProvider provider = vcsConnection.getProviderType();
        
        if (provider == EVcsProvider.GITHUB) {
            return fetchGitHubPrDetails(vcsConnection, owner, repoSlug, prNumber);
        } else if (provider == EVcsProvider.BITBUCKET_CLOUD) {
            return fetchBitbucketPrDetails(vcsConnection, owner, repoSlug, prNumber);
        } else {
            log.warn("Unsupported VCS provider for PR details fetch: {}", provider);
            return null;
        }
    }
    
    /**
     * Fetch PR details from GitHub API.
     */
    private PrDetails fetchGitHubPrDetails(VcsConnection connection, String owner, String repo, int prNumber) throws IOException {
        OkHttpClient client = vcsClientProvider.getHttpClient(connection);
        GetPullRequestAction action = new GetPullRequestAction(client);
        JsonNode prData = action.getPullRequest(owner, repo, prNumber);
        
        String sourceBranch = null;
        String targetBranch = null;
        String headCommitHash = null;
        
        if (prData.has("head")) {
            JsonNode head = prData.get("head");
            if (head.has("ref")) {
                sourceBranch = head.get("ref").asText();
            }
            if (head.has("sha")) {
                headCommitHash = head.get("sha").asText();
            }
        }
        
        if (prData.has("base") && prData.get("base").has("ref")) {
            targetBranch = prData.get("base").get("ref").asText();
        }
        
        log.info("Fetched GitHub PR details: source={}, target={}, commit={}", 
            sourceBranch, targetBranch, headCommitHash);
        
        return new PrDetails(sourceBranch, targetBranch, headCommitHash);
    }
    
    /**
     * Fetch PR details from Bitbucket Cloud API.
     */
    private PrDetails fetchBitbucketPrDetails(VcsConnection connection, String workspace, String repoSlug, int prNumber) throws IOException {
        OkHttpClient client = vcsClientProvider.getHttpClient(connection);
        org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions.GetPullRequestAction action = 
            new org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions.GetPullRequestAction(client);
        
        var prData = action.getPullRequest(workspace, repoSlug, String.valueOf(prNumber));
        
        String sourceBranch = prData.getSourceRef();
        String targetBranch = prData.getDestRef();
        // Bitbucket's GetPullRequestAction doesn't return commit hash, so we leave it null
        String headCommitHash = null;
        
        log.info("Fetched Bitbucket PR details: source={}, target={}, commit={}", 
            sourceBranch, targetBranch, headCommitHash);
        
        return new PrDetails(sourceBranch, targetBranch, headCommitHash);
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
        CommentCommandsConfig commandsConfig = config.getCommentCommandsConfig();
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
     * Uses the CommandAuthorizationService which supports multiple authorization modes:
     * - ANYONE: Allow all users
     * - PR_AUTHOR_ONLY: Only PR author can execute commands
     * - ALLOWED_USERS_ONLY: Only users in the allowed list
     * - WORKSPACE_MEMBERS: CodeCrow workspace members
     * - REPO_WRITE_ACCESS: Users with write access to the repository
     */
    private boolean isAuthorizedUser(Project project, String authorId, WebhookPayload payload) {
        AuthorizationResult result = authorizationService.checkAuthorization(
            project, 
            payload,
            payload.prAuthorId(),
            payload.prAuthorUsername()
        );
        
        if (!result.authorized()) {
            log.info("User {} not authorized for project {}: {}", authorId, project.getId(), result.reason());
        } else {
            log.debug("User {} authorized for project {}: {}", authorId, project.getId(), result.reason());
        }
        
        return result.authorized();
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
    
    /**
     * Enriches the webhook payload with PR details (branch names, commit hash) if missing.
     * This is needed for GitHub issue_comment webhooks which don't include PR details.
     */
    private WebhookPayload enrichPayloadWithPrDetails(WebhookPayload payload, Project project) {
        // If we already have all the required info, return as-is
        if (payload.sourceBranch() != null && payload.commitHash() != null) {
            return payload;
        }
        
        if (payload.pullRequestId() == null) {
            log.warn("Cannot enrich payload: no PR ID available");
            return payload;
        }
        
        try {
            VcsConnection vcsConnection = getVcsConnection(project);
            if (vcsConnection == null) {
                log.warn("Cannot enrich payload: no VCS connection for project {}", project.getId());
                return payload;
            }
            
            EVcsProvider provider = vcsConnection.getProviderType();
            VcsInfo vcsInfo = getVcsInfo(project);
            
            if (provider == EVcsProvider.GITHUB) {
                return enrichFromGitHub(payload, vcsConnection, vcsInfo);
            } else if (provider == EVcsProvider.BITBUCKET_CLOUD) {
                return enrichFromBitbucket(payload, vcsConnection, vcsInfo);
            }
            
        } catch (Exception e) {
            log.error("Failed to enrich payload with PR details: {}", e.getMessage(), e);
        }
        
        return payload;
    }
    
    private WebhookPayload enrichFromGitHub(WebhookPayload payload, VcsConnection vcsConnection, VcsInfo vcsInfo) {
        try {
            OkHttpClient client = vcsClientProvider.getHttpClient(vcsConnection);
            GetPullRequestAction action = new GetPullRequestAction(client);
            JsonNode prData = action.getPullRequest(
                    vcsInfo.workspace(),
                    vcsInfo.repoSlug(),
                    Integer.parseInt(payload.pullRequestId())
            );
            
            String sourceBranch = prData.has("head") && prData.get("head").has("ref") 
                    ? prData.get("head").get("ref").asText() : null;
            String targetBranch = prData.has("base") && prData.get("base").has("ref")
                    ? prData.get("base").get("ref").asText() : null;
            String commitHash = prData.has("head") && prData.get("head").has("sha")
                    ? prData.get("head").get("sha").asText() : null;
            
            log.info("Enriched GitHub payload: sourceBranch={}, targetBranch={}, commitHash={}", 
                    sourceBranch, targetBranch, commitHash);
            
            return payload.withEnrichedPrDetails(sourceBranch, targetBranch, commitHash);
            
        } catch (Exception e) {
            log.error("Failed to fetch GitHub PR details: {}", e.getMessage(), e);
            return payload;
        }
    }
    
    private WebhookPayload enrichFromBitbucket(WebhookPayload payload, VcsConnection vcsConnection, VcsInfo vcsInfo) {
        try {
            OkHttpClient client = vcsClientProvider.getHttpClient(vcsConnection);
            org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions.GetPullRequestAction action = 
                    new org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions.GetPullRequestAction(client);
            org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions.GetPullRequestAction.PullRequestMetadata prData = 
                    action.getPullRequest(vcsInfo.workspace(), vcsInfo.repoSlug(), payload.pullRequestId());
            
            String sourceBranch = prData.getSourceRef();
            String targetBranch = prData.getDestRef();
            // Bitbucket PullRequestMetadata doesn't expose commit hash directly, keep existing if any
            String commitHash = payload.commitHash();
            
            log.info("Enriched Bitbucket payload: sourceBranch={}, targetBranch={}", sourceBranch, targetBranch);
            
            return payload.withEnrichedPrDetails(sourceBranch, targetBranch, commitHash);
            
        } catch (Exception e) {
            log.error("Failed to fetch Bitbucket PR details: {}", e.getMessage(), e);
            return payload;
        }
    }
    
    private VcsConnection getVcsConnection(Project project) {
        // Use unified method
        return project.getEffectiveVcsConnection();
    }
    
    private record VcsInfo(String workspace, String repoSlug) {}
    
    private VcsInfo getVcsInfo(Project project) {
        // Use unified accessor
        var vcsInfo = project.getEffectiveVcsRepoInfo();
        if (vcsInfo != null) {
            return new VcsInfo(vcsInfo.getRepoWorkspace(), vcsInfo.getRepoSlug());
        }
        
        throw new IllegalStateException("No VCS binding found for project: " + project.getId());
    }
}
