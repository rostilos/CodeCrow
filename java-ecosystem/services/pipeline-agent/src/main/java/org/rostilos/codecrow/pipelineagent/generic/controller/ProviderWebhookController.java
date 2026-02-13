package org.rostilos.codecrow.pipelineagent.generic.controller;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.job.JobStatus;
import org.rostilos.codecrow.core.model.job.JobTriggerSource;
import org.rostilos.codecrow.core.model.job.JobType;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.service.JobService;
import org.rostilos.codecrow.pipelineagent.bitbucket.webhookhandler.BitbucketCloudWebhookParser;
import org.rostilos.codecrow.pipelineagent.github.webhookhandler.GitHubWebhookParser;
import org.rostilos.codecrow.pipelineagent.gitlab.webhookhandler.GitLabWebhookParser;
import org.rostilos.codecrow.pipelineagent.generic.dto.webhook.WebhookPayload;
import org.rostilos.codecrow.pipelineagent.generic.webhookhandler.WebhookProjectResolver;
import org.rostilos.codecrow.pipelineagent.generic.webhookhandler.WebhookHandler;
import org.rostilos.codecrow.pipelineagent.generic.webhookhandler.WebhookHandlerFactory;
import org.rostilos.codecrow.pipelineagent.generic.processor.WebhookAsyncProcessor;
import org.rostilos.codecrow.core.service.BaseUrlSettingsReader;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.Map;
import java.util.Optional;

/**
 * Provider-aware webhook controller for VCS integrations.
 * Receives webhooks from VCS providers and routes them to the appropriate handler.
 * 
 * Webhook URL format: /api/webhooks/{provider}/{authToken}
 * 
 * The authToken is used to authenticate the webhook request and identify the project.
 */
@RestController
@RequestMapping("/api/webhooks")
public class ProviderWebhookController {
    
    private static final Logger log = LoggerFactory.getLogger(ProviderWebhookController.class);
    
    private final WebhookProjectResolver projectResolver;
    private final BitbucketCloudWebhookParser bitbucketParser;
    private final GitHubWebhookParser githubParser;
    private final GitLabWebhookParser gitlabParser;
    private final ObjectMapper objectMapper;
    private final WebhookHandlerFactory webhookHandlerFactory;
    private final JobService jobService;
    private final WebhookAsyncProcessor webhookAsyncProcessor;
    private final BaseUrlSettingsReader baseUrlSettingsReader;
    
    public ProviderWebhookController(
            WebhookProjectResolver projectResolver,
            BitbucketCloudWebhookParser bitbucketParser,
            GitHubWebhookParser githubParser,
            GitLabWebhookParser gitlabParser,
            ObjectMapper objectMapper,
            WebhookHandlerFactory webhookHandlerFactory,
            JobService jobService,
            WebhookAsyncProcessor webhookAsyncProcessor,
            BaseUrlSettingsReader baseUrlSettingsReader
    ) {
        this.projectResolver = projectResolver;
        this.bitbucketParser = bitbucketParser;
        this.githubParser = githubParser;
        this.gitlabParser = gitlabParser;
        this.objectMapper = objectMapper;
        this.webhookHandlerFactory = webhookHandlerFactory;
        this.jobService = jobService;
        this.webhookAsyncProcessor = webhookAsyncProcessor;
        this.baseUrlSettingsReader = baseUrlSettingsReader;
    }
    
    /**
     * Handle incoming webhook from any VCS provider.
     * 
     * @param provider The VCS provider (e.g., "bitbucket-cloud", "github", "gitlab")
     * @param authToken The project auth token for authentication
     * @param bitbucketEventType The event type header (X-Event-Key for Bitbucket, X-GitHub-Event for GitHub)
     * @param body The raw webhook payload
     */
    @PostMapping(value = "/{provider}/{authToken}", consumes = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<?> handleWebhook(
            @PathVariable String provider,
            @PathVariable String authToken,
            @RequestHeader(value = "X-Event-Key", required = false) String bitbucketEventType,
            @RequestHeader(value = "X-GitHub-Event", required = false) String githubEventType,
            @RequestHeader(value = "X-Gitlab-Event", required = false) String gitlabEventType,
            @RequestBody String body
    ) {
        try {
            EVcsProvider vcsProvider = EVcsProvider.fromId(provider);
            String eventType = getEventType(vcsProvider, bitbucketEventType, githubEventType, gitlabEventType);
            
            log.info("Received {} webhook: eventType={}", provider, eventType);
            
            JsonNode payload = objectMapper.readTree(body);
            
            // Parse webhook payload based on provider
            WebhookPayload webhookPayload = parsePayload(vcsProvider, eventType, payload);
            
            if (webhookPayload.externalRepoId() == null) {
                log.warn("Could not extract repository ID from webhook payload");
                return ResponseEntity.badRequest()
                        .body(Map.of("error", "missing_repo_id", "message", "Could not extract repository ID"));
            }
            
            // Find the project by external repo ID
            Optional<Project> projectOpt = projectResolver.findProjectByExternalRepo(
                    vcsProvider, webhookPayload.externalRepoId());
            
            if (projectOpt.isEmpty()) {
                log.warn("No project found for {} repo {}", provider, webhookPayload.externalRepoId());
                return ResponseEntity.status(HttpStatus.NOT_FOUND)
                        .body(Map.of("error", "project_not_found", 
                                     "message", "No project configured for this repository"));
            }
            
            Project project = projectOpt.get();
            
            // Validate auth token
            if (!projectResolver.validateWebhookAuth(project, authToken)) {
                log.warn("Invalid auth token for project {}", project.getId());
                return ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                        .body(Map.of("error", "unauthorized", "message", "Invalid auth token"));
            }
            
            // Process the webhook
            return processWebhook(vcsProvider, webhookPayload, project);
            
        } catch (IllegalArgumentException e) {
            log.warn("Unknown provider: {}", provider);
            return ResponseEntity.badRequest()
                    .body(Map.of("error", "unknown_provider", "message", "Unknown VCS provider: " + provider));
        } catch (Exception e) {
            log.error("Error processing webhook", e);
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                    .body(Map.of("error", "processing_error", "message", e.getMessage()));
        }
    }
    
    /**
     * Alternative webhook endpoint without auth token in URL.
     * Uses the repository ID to look up the project, then validates via signature.
     * This is for providers that don't support custom webhook URLs.
     */
    @PostMapping(value = "/{provider}", consumes = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<?> handleWebhookWithoutToken(
            @PathVariable String provider,
            @RequestHeader(value = "X-Event-Key", required = false) String bitbucketEventType,
            @RequestHeader(value = "X-GitHub-Event", required = false) String githubEventType,
            @RequestHeader(value = "X-Gitlab-Event", required = false) String gitlabEventType,
            @RequestHeader(value = "X-Hub-Signature-256", required = false) String githubSignature,
            @RequestBody String body
    ) {
        try {
            EVcsProvider vcsProvider = EVcsProvider.fromId(provider);
            String eventType = getEventType(vcsProvider, bitbucketEventType, githubEventType, gitlabEventType);
            
            log.info("Received {} webhook (no token): eventType={}", provider, eventType);
            
            JsonNode payload = objectMapper.readTree(body);
            WebhookPayload webhookPayload = parsePayload(vcsProvider, eventType, payload);
            
            if (webhookPayload.externalRepoId() == null) {
                log.warn("Could not extract repository ID from webhook payload");
                return ResponseEntity.badRequest()
                        .body(Map.of("error", "missing_repo_id", "message", "Could not extract repository ID"));
            }
            
            Optional<Project> projectOpt = projectResolver.findProjectByExternalRepo(
                    vcsProvider, webhookPayload.externalRepoId());
            
            if (projectOpt.isEmpty()) {
                log.info("No project found for {} repo {} - ignoring webhook", 
                        provider, webhookPayload.externalRepoId());
                return ResponseEntity.ok(Map.of("status", "ignored", 
                        "message", "Repository not configured"));
            }
            
            Project project = projectOpt.get();
            
            // TODO: Validate signature for providers that support it (GitHub, GitLab)
            // TODO: For now, we accept all webhooks for configured repos
            
            return processWebhook(vcsProvider, webhookPayload, project);
            
        } catch (IllegalArgumentException e) {
            log.warn("Unknown provider: {}", provider);
            return ResponseEntity.badRequest()
                    .body(Map.of("error", "unknown_provider", "message", "Unknown VCS provider: " + provider));
        } catch (Exception e) {
            log.error("Error processing webhook", e);
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                    .body(Map.of("error", "processing_error", "message", e.getMessage()));
        }
    }
    
    private String getEventType(EVcsProvider provider, String bitbucketEvent, String githubEvent, String gitlabEvent) {
        return switch (provider) {
            case BITBUCKET_CLOUD, BITBUCKET_SERVER -> bitbucketEvent;
            case GITHUB -> githubEvent;
            case GITLAB -> gitlabEvent;
        };
    }
    
    private WebhookPayload parsePayload(EVcsProvider provider, String eventType, JsonNode payload) {
        return switch (provider) {
            case BITBUCKET_CLOUD -> bitbucketParser.parse(eventType, payload);
            case BITBUCKET_SERVER -> throw new UnsupportedOperationException("Bitbucket Server not yet implemented");
            case GITHUB -> githubParser.parse(eventType, payload);
            case GITLAB -> gitlabParser.parse(eventType, payload);
        };
    }
    
    private ResponseEntity<?> processWebhook(EVcsProvider provider, WebhookPayload payload, Project project) {
        log.info("Processing {} webhook for project {}: repo={}, event={}", 
                provider, project.getId(), payload.getFullRepoName(), payload.eventType());
        
        // Log webhook details
        if (payload.isPullRequestEvent()) {
            log.info("  Pull Request: {} ({} -> {})", 
                    payload.pullRequestId(), payload.sourceBranch(), payload.targetBranch());
        } else if (payload.isPushEvent()) {
            log.info("  Push: branch={}, commit={}", payload.sourceBranch(), payload.commitHash());
        }
        
        // Find the appropriate handler for this event
        Optional<WebhookHandler> handlerOpt = webhookHandlerFactory.getHandler(provider, payload.eventType());
        
        if (handlerOpt.isEmpty()) {
            log.info("No handler found for {} event: {} - ignoring", provider, payload.eventType());
            return ResponseEntity.ok(Map.of(
                    "status", "ignored",
                    "message", "Event type not handled: " + payload.eventType(),
                    "projectId", project.getId(),
                    "eventType", payload.eventType()
            ));
        }
        
        // For comment events without CodeCrow commands, ignore immediately without creating a Job
        // This prevents DB clutter from non-command comments
        if (payload.isCommentEvent() && !payload.hasCodecrowCommand()) {
            log.info("Comment event without CodeCrow command - ignoring without creating Job");
            return ResponseEntity.ok(Map.of(
                    "status", "ignored",
                    "message", "Not a CodeCrow command comment",
                    "projectId", project.getId(),
                    "eventType", payload.eventType()
            ));
        }
        
        // Create a Job for tracking
        Job job = createJobForWebhook(payload, project);
        
        // Return immediately with job link
        String jobUrl = buildJobUrl(project, job);
        String logsStreamUrl = buildJobLogsStreamUrl(job);
        
        log.info("Dispatching webhook to async processor: job={}, event={}", 
                job.getExternalId(), payload.eventType());
        
        // Process webhook asynchronously with proper transactional context
        webhookAsyncProcessor.processWebhookAsync(
            provider, 
            project.getId(), 
            payload, 
            handlerOpt.get(), 
            job
        );
        
        log.info("Webhook dispatched to async processor: job={}", job.getExternalId());
        
        return ResponseEntity.accepted().body(Map.of(
                "status", "accepted",
                "message", "Webhook received, processing started",
                "jobId", job.getExternalId(),
                "jobUrl", jobUrl,
                "logsStreamUrl", logsStreamUrl,
                "projectId", project.getId(),
                "eventType", payload.eventType()
        ));
    }
    
    /**
     * Create a Job record for the webhook event.
     */
    private Job createJobForWebhook(WebhookPayload payload, Project project) {
        // Check if this is a comment command event
        if (payload.isCommentEvent() && payload.hasCodecrowCommand()) {
            WebhookPayload.CodecrowCommand command = payload.getCodecrowCommand();
            JobType commandJobType = switch (command.type()) {
                case SUMMARIZE -> JobType.SUMMARIZE_COMMAND;
                case ASK -> JobType.ASK_COMMAND;
                case ANALYZE -> JobType.ANALYZE_COMMAND;
                case REVIEW -> JobType.REVIEW_COMMAND;
            };
            
            Long prNumber = payload.pullRequestId() != null ? Long.parseLong(payload.pullRequestId()) : null;
            return jobService.createCommandJob(
                    project,
                    commandJobType,
                    prNumber,
                    payload.commitHash(),
                    JobTriggerSource.WEBHOOK
            );
        }
        
        // PR merge events (pullrequest:fulfilled) should be treated as branch analysis, not PR analysis
        // because they update the target branch, not review the PR
        String eventType = payload.eventType();
        boolean isPrMergeEvent = "pullrequest:fulfilled".equals(eventType) || 
                                 "pull_request.closed".equals(eventType) && payload.rawPayload() != null &&
                                 payload.rawPayload().path("pull_request").path("merged").asBoolean(false);
        
        if (payload.isPullRequestEvent() && !isPrMergeEvent) {
            // PR created/updated - actual PR analysis
            return jobService.createPrAnalysisJob(
                    project,
                    Long.parseLong(payload.pullRequestId()),
                    payload.sourceBranch(),
                    payload.targetBranch(),
                    payload.commitHash(),
                    JobTriggerSource.WEBHOOK,
                    null  // No user for webhook triggers
            );
        } else if (payload.isPushEvent() || isPrMergeEvent) {
            // Push event or PR merge - branch analysis
            String branchName = isPrMergeEvent ? payload.targetBranch() : payload.sourceBranch();
            return jobService.createBranchAnalysisJob(
                    project,
                    branchName,
                    payload.commitHash(),
                    JobTriggerSource.WEBHOOK,
                    null
            );
        } else {
            // Generic job for other event types
            Job job = new Job();
            job.setProject(project);
            job.setJobType(JobType.MANUAL_ANALYSIS);
            job.setTriggerSource(JobTriggerSource.WEBHOOK);
            job.setTitle("Webhook: " + payload.eventType());
            job.setStatus(JobStatus.PENDING);
            // Save through service would be better, but for now:
            return job;
        }
    }
    
    private String buildJobUrl(Project project, Job job) {
        return String.format("%s/api/%s/projects/%s/jobs/%s",
                baseUrlSettingsReader.getBaseUrls().frontendUrl(),
                project.getWorkspace().getSlug(),
                project.getNamespace(),
                job.getExternalId());
    }
    
    private String buildJobLogsStreamUrl(Job job) {
        return String.format("%s/api/jobs/%s/logs/stream",
                baseUrlSettingsReader.getBaseUrls().frontendUrl(), job.getExternalId());
    }
}
