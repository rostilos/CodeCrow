package org.rostilos.codecrow.pipelineagent.generic.service;

import org.rostilos.codecrow.core.model.analysis.CommentCommandRateLimit;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.CommentCommandsConfig;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.persistence.repository.analysis.CommentCommandRateLimitRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.OffsetDateTime;
import java.time.temporal.ChronoUnit;
import java.util.Optional;

/**
 * Service for managing rate limiting of comment commands at the project level.
 */
@Service
public class CommentCommandRateLimitService {
    
    private static final Logger log = LoggerFactory.getLogger(CommentCommandRateLimitService.class);
    
    private final CommentCommandRateLimitRepository rateLimitRepository;
    
    public CommentCommandRateLimitService(CommentCommandRateLimitRepository rateLimitRepository) {
        this.rateLimitRepository = rateLimitRepository;
    }
    
    /**
     * Check if a command is allowed for the given project based on rate limits.
     * 
     * @param project The project to check
     * @return true if the command is allowed, false if rate limited
     */
    @Transactional(readOnly = true)
    public boolean isCommandAllowed(Project project) {
        ProjectConfig config = project.getConfiguration();
        if (config == null || !config.isCommentCommandsEnabled()) {
            return false;
        }
        
        CommentCommandsConfig commandsConfig = config.getCommentCommandsConfig();
        int rateLimit = commandsConfig.getEffectiveRateLimit();
        int windowMinutes = commandsConfig.getEffectiveRateLimitWindowMinutes();
        
        OffsetDateTime windowStart = OffsetDateTime.now().minus(windowMinutes, ChronoUnit.MINUTES);
        int commandCount = rateLimitRepository.countCommandsInWindow(project.getId(), windowStart);
        
        return commandCount < rateLimit;
    }
    
    /**
     * Record a command execution for rate limiting purposes.
     * Uses atomic upsert to avoid race conditions with concurrent requests.
     * 
     * @param project The project that executed the command
     */
    @Transactional
    public void recordCommand(Project project) {
        ProjectConfig config = project.getConfiguration();
        int windowMinutes = config != null && config.getCommentCommandsConfig() != null
            ? config.getCommentCommandsConfig().getEffectiveRateLimitWindowMinutes()
            : CommentCommandsConfig.DEFAULT_RATE_LIMIT_WINDOW_MINUTES;
        
        OffsetDateTime windowStart = OffsetDateTime.now()
            .truncatedTo(ChronoUnit.HOURS)
            .plus((OffsetDateTime.now().getMinute() / windowMinutes) * windowMinutes, ChronoUnit.MINUTES);
        
        // Use atomic upsert to avoid duplicate key violations from concurrent requests
        rateLimitRepository.upsertCommandCount(project.getId(), windowStart);
        
        log.debug("Recorded command for project {}, window starting at {}", project.getId(), windowStart);
    }
    
    /**
     * Get the remaining command allowance for a project.
     * 
     * @param project The project to check
     * @return The number of commands remaining in the current window
     */
    @Transactional(readOnly = true)
    public int getRemainingAllowance(Project project) {
        ProjectConfig config = project.getConfiguration();
        if (config == null || !config.isCommentCommandsEnabled()) {
            return 0;
        }
        
        CommentCommandsConfig commandsConfig = config.getCommentCommandsConfig();
        int rateLimit = commandsConfig.getEffectiveRateLimit();
        int windowMinutes = commandsConfig.getEffectiveRateLimitWindowMinutes();
        
        OffsetDateTime windowStart = OffsetDateTime.now().minus(windowMinutes, ChronoUnit.MINUTES);
        int commandCount = rateLimitRepository.countCommandsInWindow(project.getId(), windowStart);
        
        return Math.max(0, rateLimit - commandCount);
    }
    
    /**
     * Get the time in seconds until the rate limit resets.
     * 
     * @param project The project to check
     * @return Seconds until reset, or 0 if no active rate limit
     */
    @Transactional(readOnly = true)
    public long getSecondsUntilReset(Project project) {
        Optional<CommentCommandRateLimit> latestRecord = rateLimitRepository.findLatestByProjectId(project.getId());
        
        if (latestRecord.isEmpty()) {
            return 0;
        }
        
        ProjectConfig config = project.getConfiguration();
        int windowMinutes = config != null && config.getCommentCommandsConfig() != null
            ? config.getCommentCommandsConfig().getEffectiveRateLimitWindowMinutes()
            : CommentCommandsConfig.DEFAULT_RATE_LIMIT_WINDOW_MINUTES;
        
        OffsetDateTime windowEnd = latestRecord.get().getWindowStart().plus(windowMinutes, ChronoUnit.MINUTES);
        OffsetDateTime now = OffsetDateTime.now();
        
        if (now.isAfter(windowEnd)) {
            return 0;
        }
        
        return ChronoUnit.SECONDS.between(now, windowEnd);
    }
    
    /**
     * Clean up old rate limit records.
     * Runs every hour to remove records older than 24 hours.
     */
    @Scheduled(fixedRate = 3600000) // Every hour
    @Transactional
    public void cleanupOldRecords() {
        OffsetDateTime cutoff = OffsetDateTime.now().minus(24, ChronoUnit.HOURS);
        int deleted = rateLimitRepository.deleteOldRecords(cutoff);
        
        if (deleted > 0) {
            log.info("Cleaned up {} old rate limit records", deleted);
        }
    }
    
    /**
     * Result class for rate limit check operations.
     */
    public record RateLimitCheckResult(
        boolean allowed,
        int remaining,
        long secondsUntilReset,
        String message
    ) {
        public static RateLimitCheckResult allowed(int remaining) {
            return new RateLimitCheckResult(true, remaining, 0, "Command allowed");
        }
        
        public static RateLimitCheckResult denied(int remaining, long secondsUntilReset) {
            return new RateLimitCheckResult(
                false, 
                remaining, 
                secondsUntilReset,
                String.format("Rate limit exceeded. Try again in %d seconds.", secondsUntilReset)
            );
        }
        
        public static RateLimitCheckResult disabled() {
            return new RateLimitCheckResult(false, 0, 0, "Comment commands are not enabled for this project");
        }
    }
    
    /**
     * Comprehensive rate limit check that returns detailed information.
     */
    @Transactional(readOnly = true)
    public RateLimitCheckResult checkRateLimit(Project project) {
        ProjectConfig config = project.getConfiguration();
        if (config == null || !config.isCommentCommandsEnabled()) {
            return RateLimitCheckResult.disabled();
        }
        
        int remaining = getRemainingAllowance(project);
        
        if (remaining > 0) {
            return RateLimitCheckResult.allowed(remaining);
        } else {
            long secondsUntilReset = getSecondsUntilReset(project);
            return RateLimitCheckResult.denied(0, secondsUntilReset);
        }
    }
}
