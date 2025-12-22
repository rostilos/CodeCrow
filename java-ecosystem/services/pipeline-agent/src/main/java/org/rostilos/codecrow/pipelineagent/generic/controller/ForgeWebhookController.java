package org.rostilos.codecrow.pipelineagent.generic.controller;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.job.JobTriggerSource;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.persistence.repository.vcs.VcsConnectionRepository;
import org.rostilos.codecrow.core.service.JobService;
import org.rostilos.codecrow.pipelineagent.generic.webhook.WebhookPayload;
import org.rostilos.codecrow.pipelineagent.generic.webhook.WebhookProjectResolver;
import org.rostilos.codecrow.pipelineagent.generic.webhook.handler.WebhookHandler;
import org.rostilos.codecrow.pipelineagent.generic.webhook.handler.WebhookHandlerFactory;
import org.rostilos.codecrow.pipelineagent.generic.service.WebhookAsyncProcessor;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.security.GeneralSecurityException;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.Base64;
import java.util.List;
import java.util.Map;
import java.util.Optional;

/**
 * Controller for handling Atlassian Forge webhook events in pipeline-agent.
 * 
 * Receives Bitbucket events (push, PR, etc.) from Forge and triggers analysis.
 * Each request includes appSystemToken which is used to call Bitbucket API.
 * 
 * Headers:
 * - Authorization: Bearer <FIT token> - Forge Invocation Token
 * - x-forge-oauth-system: <token> - OAuth token for Bitbucket API calls
 */
@RestController
@RequestMapping("/api/forge")
public class ForgeWebhookController {

    private static final Logger log = LoggerFactory.getLogger(ForgeWebhookController.class);

    private final WebhookProjectResolver projectResolver;
    private final WebhookHandlerFactory webhookHandlerFactory;
    private final JobService jobService;
    private final WebhookAsyncProcessor webhookAsyncProcessor;
    private final VcsConnectionRepository vcsConnectionRepository;
    private final TokenEncryptionService encryptionService;
    private final ObjectMapper objectMapper;

    public ForgeWebhookController(
            WebhookProjectResolver projectResolver,
            WebhookHandlerFactory webhookHandlerFactory,
            JobService jobService,
            WebhookAsyncProcessor webhookAsyncProcessor,
            VcsConnectionRepository vcsConnectionRepository,
            TokenEncryptionService encryptionService
    ) {
        this.projectResolver = projectResolver;
        this.webhookHandlerFactory = webhookHandlerFactory;
        this.jobService = jobService;
        this.webhookAsyncProcessor = webhookAsyncProcessor;
        this.vcsConnectionRepository = vcsConnectionRepository;
        this.encryptionService = encryptionService;
        this.objectMapper = new ObjectMapper();
    }

    /**
     * Handle push events from Forge.
     * Event: avi:bitbucket:push:repository
     */
    @PostMapping(value = "/webhook/push", consumes = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<?> handlePush(
            @RequestBody JsonNode payload,
            @RequestHeader(value = "Authorization", required = false) String authorization,
            @RequestHeader(value = "x-forge-oauth-system", required = false) String appSystemToken
    ) {
        log.info("Received Forge push event");
        return processForgeEvent("push", "avi:bitbucket:push:repository", payload, authorization, appSystemToken);
    }

    /**
     * Handle PR created events from Forge.
     * Event: avi:bitbucket:created:pullrequest
     */
    @PostMapping(value = "/webhook/pullrequest/created", consumes = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<?> handlePrCreated(
            @RequestBody JsonNode payload,
            @RequestHeader(value = "Authorization", required = false) String authorization,
            @RequestHeader(value = "x-forge-oauth-system", required = false) String appSystemToken
    ) {
        log.info("Received Forge PR created event");
        return processForgeEvent("pullrequest:created", "avi:bitbucket:created:pullrequest", payload, authorization, appSystemToken);
    }

    /**
     * Handle PR updated events from Forge.
     * Event: avi:bitbucket:updated:pullrequest
     */
    @PostMapping(value = "/webhook/pullrequest/updated", consumes = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<?> handlePrUpdated(
            @RequestBody JsonNode payload,
            @RequestHeader(value = "Authorization", required = false) String authorization,
            @RequestHeader(value = "x-forge-oauth-system", required = false) String appSystemToken
    ) {
        log.info("Received Forge PR updated event");
        return processForgeEvent("pullrequest:updated", "avi:bitbucket:updated:pullrequest", payload, authorization, appSystemToken);
    }

    /**
     * Handle PR fulfilled (merged) events from Forge.
     * Event: avi:bitbucket:fulfilled:pullrequest
     */
    @PostMapping(value = "/webhook/pullrequest/fulfilled", consumes = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<?> handlePrFulfilled(
            @RequestBody JsonNode payload,
            @RequestHeader(value = "Authorization", required = false) String authorization,
            @RequestHeader(value = "x-forge-oauth-system", required = false) String appSystemToken
    ) {
        log.info("Received Forge PR fulfilled event");
        // PR merged - could trigger branch analysis or cleanup
        return ResponseEntity.ok(Map.of("status", "acknowledged", "event", "pullrequest:fulfilled"));
    }

    /**
     * Handle PR rejected (declined) events from Forge.
     * Event: avi:bitbucket:rejected:pullrequest
     */
    @PostMapping(value = "/webhook/pullrequest/rejected", consumes = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<?> handlePrRejected(
            @RequestBody JsonNode payload,
            @RequestHeader(value = "Authorization", required = false) String authorization,
            @RequestHeader(value = "x-forge-oauth-system", required = false) String appSystemToken
    ) {
        log.info("Received Forge PR rejected event");
        // PR declined - cleanup if needed
        return ResponseEntity.ok(Map.of("status", "acknowledged", "event", "pullrequest:rejected"));
    }

    /**
     * Handle PR comment events from Forge.
     * Event: avi:bitbucket:created:pullrequest-comment
     */
    @PostMapping(value = "/webhook/pullrequest/comment", consumes = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<?> handlePrComment(
            @RequestBody JsonNode payload,
            @RequestHeader(value = "Authorization", required = false) String authorization,
            @RequestHeader(value = "x-forge-oauth-system", required = false) String appSystemToken
    ) {
        log.info("Received Forge PR comment event");
        // Could implement reply-to-comment functionality
        return ResponseEntity.ok(Map.of("status", "acknowledged", "event", "pullrequest:comment"));
    }

    /**
     * Process a Forge webhook event.
     */
    private ResponseEntity<?> processForgeEvent(
            String eventType,
            String forgeEventType,
            JsonNode payload,
            String authorization,
            String appSystemToken
    ) {
        try {
            // Extract workspace ID from FIT token or payload
            String fitToken = extractFitToken(authorization);
            String workspaceId = extractWorkspaceId(payload, fitToken);
            
            if (workspaceId == null) {
                log.warn("Could not extract workspace ID from Forge event");
                return ResponseEntity.badRequest()
                        .body(Map.of("error", "missing_workspace", "message", "Could not identify workspace"));
            }

            // Update stored token if we have a fresh one
            if (appSystemToken != null && !appSystemToken.isEmpty()) {
                updateStoredToken(workspaceId, appSystemToken);
            }

            // Parse webhook payload into standard format
            WebhookPayload webhookPayload = parseForgePayload(eventType, payload);
            
            if (webhookPayload == null || webhookPayload.externalRepoId() == null) {
                log.warn("Could not parse repository info from Forge payload");
                return ResponseEntity.ok(Map.of(
                        "status", "ignored",
                        "reason", "Could not parse repository information"
                ));
            }

            // Find project by repo
            Optional<Project> projectOpt = projectResolver.findProjectByExternalRepo(
                    EVcsProvider.BITBUCKET_CLOUD, webhookPayload.externalRepoId());
            
            if (projectOpt.isEmpty()) {
                log.info("No project configured for repo {} - event ignored", webhookPayload.externalRepoId());
                return ResponseEntity.ok(Map.of(
                        "status", "ignored",
                        "reason", "No project configured for this repository"
                ));
            }

            Project project = projectOpt.get();

            // Get handler for this event type
            String bitbucketEventType = mapForgeToBitbucketEvent(forgeEventType);
            Optional<WebhookHandler> handlerOpt = webhookHandlerFactory.getHandler(EVcsProvider.BITBUCKET_CLOUD, bitbucketEventType);
            
            if (handlerOpt.isEmpty()) {
                log.info("No handler for event type: {}", bitbucketEventType);
                return ResponseEntity.ok(Map.of(
                        "status", "ignored",
                        "reason", "Event type not supported: " + bitbucketEventType
                ));
            }

            // Create job and process async
            Job job = createJob(project, webhookPayload);
            
            webhookAsyncProcessor.processWebhookAsync(
                    EVcsProvider.BITBUCKET_CLOUD,
                    project.getId(),
                    webhookPayload,
                    handlerOpt.get(),
                    job
            );

            return ResponseEntity.accepted().body(Map.of(
                    "status", "processing",
                    "jobId", job.getId(),
                    "message", "Analysis started"
            ));

        } catch (Exception e) {
            log.error("Error processing Forge event: {}", eventType, e);
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                    .body(Map.of("error", "processing_failed", "message", e.getMessage()));
        }
    }

    /**
     * Update the stored token for a workspace.
     * Forge sends fresh tokens with each event.
     */
    private void updateStoredToken(String workspaceId, String appSystemToken) {
        try {
            List<VcsConnection> connections = vcsConnectionRepository
                    .findByProviderTypeAndExternalWorkspaceId(EVcsProvider.BITBUCKET_CLOUD, workspaceId);
            
            for (VcsConnection conn : connections) {
                if (conn.getConnectionType() == EVcsConnectionType.FORGE_APP) {
                    String encryptedToken = encryptionService.encrypt(appSystemToken);
                    LocalDateTime tokenExpiry = extractTokenExpiry(appSystemToken);
                    
                    conn.setAccessToken(encryptedToken);
                    conn.setTokenExpiresAt(tokenExpiry);
                    vcsConnectionRepository.save(conn);
                    
                    log.debug("Updated token for VcsConnection {}", conn.getId());
                }
            }
        } catch (GeneralSecurityException e) {
            log.warn("Failed to update token for workspace {}: {}", workspaceId, e.getMessage());
        }
    }

    /**
     * Parse Forge payload into standard WebhookPayload.
     */
    private WebhookPayload parseForgePayload(String eventType, JsonNode payload) {
        try {
            // Forge wraps the actual event in a "payload" field
            JsonNode eventPayload = payload.has("payload") ? payload.get("payload") : payload;
            
            // Extract repository info
            JsonNode repository = null;
            if (eventPayload.has("repository")) {
                repository = eventPayload.get("repository");
            } else if (eventPayload.has("event") && eventPayload.get("event").has("repository")) {
                repository = eventPayload.get("event").get("repository");
            }
            
            if (repository == null) {
                return null;
            }

            String repoUuid = repository.has("uuid") 
                    ? repository.get("uuid").asText().replace("{", "").replace("}", "")
                    : null;
            String repoFullName = repository.has("full_name") 
                    ? repository.get("full_name").asText() 
                    : null;
            String repoSlug = repository.has("name")
                    ? repository.get("name").asText()
                    : repoFullName != null && repoFullName.contains("/") 
                            ? repoFullName.split("/")[1] 
                            : null;
            String workspaceSlug = repoFullName != null && repoFullName.contains("/")
                    ? repoFullName.split("/")[0]
                    : null;

            // Extract PR info if applicable
            String pullRequestId = null;
            String sourceBranch = null;
            String targetBranch = null;
            String commitHash = null;
            
            JsonNode pullRequest = null;
            if (eventPayload.has("pullrequest")) {
                pullRequest = eventPayload.get("pullrequest");
            } else if (eventPayload.has("event") && eventPayload.get("event").has("pullrequest")) {
                pullRequest = eventPayload.get("event").get("pullrequest");
            }
            
            if (pullRequest != null) {
                pullRequestId = pullRequest.has("id") ? String.valueOf(pullRequest.get("id").asInt()) : null;
                
                if (pullRequest.has("source") && pullRequest.get("source").has("branch")) {
                    sourceBranch = pullRequest.get("source").get("branch").get("name").asText();
                }
                if (pullRequest.has("destination") && pullRequest.get("destination").has("branch")) {
                    targetBranch = pullRequest.get("destination").get("branch").get("name").asText();
                }
                if (pullRequest.has("source") && pullRequest.get("source").has("commit")) {
                    commitHash = pullRequest.get("source").get("commit").get("hash").asText();
                }
            }

            // For push events, extract branch info
            if (eventType.equals("push") && eventPayload.has("push")) {
                JsonNode push = eventPayload.get("push");
                if (push.has("changes") && push.get("changes").isArray() && push.get("changes").size() > 0) {
                    JsonNode change = push.get("changes").get(0);
                    if (change.has("new") && change.get("new").has("name")) {
                        targetBranch = change.get("new").get("name").asText();
                    }
                    if (change.has("commits") && change.get("commits").isArray() && change.get("commits").size() > 0) {
                        commitHash = change.get("commits").get(0).get("hash").asText();
                    }
                }
            }

            return new WebhookPayload(
                    EVcsProvider.BITBUCKET_CLOUD,  // provider
                    eventType,                      // eventType
                    repoUuid,                       // externalRepoId
                    repoSlug,                       // repoSlug
                    workspaceSlug,                  // workspaceSlug
                    pullRequestId,                  // pullRequestId
                    sourceBranch,                   // sourceBranch
                    targetBranch,                   // targetBranch
                    commitHash,                     // commitHash
                    eventPayload,                   // rawPayload (JsonNode)
                    null                            // commentData (null for now)
            );
            
        } catch (Exception e) {
            log.error("Error parsing Forge payload", e);
            return null;
        }
    }

    /**
     * Map Forge event type to Bitbucket event type for handler lookup.
     */
    private String mapForgeToBitbucketEvent(String forgeEvent) {
        return switch (forgeEvent) {
            case "avi:bitbucket:push:repository" -> "repo:push";
            case "avi:bitbucket:created:pullrequest" -> "pullrequest:created";
            case "avi:bitbucket:updated:pullrequest" -> "pullrequest:updated";
            case "avi:bitbucket:fulfilled:pullrequest" -> "pullrequest:fulfilled";
            case "avi:bitbucket:rejected:pullrequest" -> "pullrequest:rejected";
            case "avi:bitbucket:created:pullrequest-comment" -> "pullrequest:comment_created";
            default -> forgeEvent;
        };
    }

    /**
     * Create a job for the webhook event using JobService.
     */
    private Job createJob(Project project, WebhookPayload payload) {
        if (payload.pullRequestId() != null && !payload.pullRequestId().isEmpty()) {
            // PR analysis job
            return jobService.createPrAnalysisJob(
                    project,
                    Long.parseLong(payload.pullRequestId()),
                    payload.sourceBranch(),
                    payload.targetBranch(),
                    payload.commitHash(),
                    JobTriggerSource.WEBHOOK,
                    null // No user for webhook-triggered jobs
            );
        } else {
            // Branch analysis job
            return jobService.createBranchAnalysisJob(
                    project,
                    payload.targetBranch(),
                    payload.commitHash(),
                    JobTriggerSource.WEBHOOK,
                    null
            );
        }
    }

    private String extractFitToken(String authorization) {
        if (authorization != null && authorization.startsWith("Bearer ")) {
            return authorization.substring(7);
        }
        return null;
    }

    private String extractWorkspaceId(JsonNode payload, String fitToken) {
        // From FIT token
        if (fitToken != null) {
            try {
                String[] parts = fitToken.split("\\.");
                if (parts.length >= 2) {
                    String claims = new String(Base64.getUrlDecoder().decode(parts[1]));
                    JsonNode fitClaims = objectMapper.readTree(claims);
                    if (fitClaims.has("app") && fitClaims.get("app").has("apiBaseUrl")) {
                        String apiBaseUrl = fitClaims.get("app").get("apiBaseUrl").asText();
                        String[] urlParts = apiBaseUrl.split("/");
                        return urlParts[urlParts.length - 1];
                    }
                }
            } catch (Exception e) {
                log.debug("Could not extract workspace from FIT: {}", e.getMessage());
            }
        }
        
        // From payload
        JsonNode eventPayload = payload.has("payload") ? payload.get("payload") : payload;
        
        if (eventPayload.has("repository") && eventPayload.get("repository").has("workspace")) {
            JsonNode workspace = eventPayload.get("repository").get("workspace");
            if (workspace.has("uuid")) {
                return workspace.get("uuid").asText().replace("{", "").replace("}", "");
            }
        }
        
        return null;
    }

    private LocalDateTime extractTokenExpiry(String token) {
        try {
            String[] parts = token.split("\\.");
            if (parts.length >= 2) {
                String payload = new String(Base64.getUrlDecoder().decode(parts[1]));
                JsonNode claims = objectMapper.readTree(payload);
                if (claims.has("exp")) {
                    long exp = claims.get("exp").asLong();
                    return LocalDateTime.ofInstant(Instant.ofEpochSecond(exp), ZoneId.systemDefault());
                }
            }
        } catch (Exception e) {
            log.debug("Failed to extract token expiry: {}", e.getMessage());
        }
        return LocalDateTime.now().plusHours(1);
    }
}
