package org.rostilos.codecrow.notification.scheduler;

import org.rostilos.codecrow.events.notification.TokenExpiringEvent;
import org.rostilos.codecrow.notification.config.NotificationProperties;
import org.rostilos.codecrow.notification.repository.NotificationRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import jakarta.persistence.EntityManager;
import jakarta.persistence.Query;
import java.time.OffsetDateTime;
import java.time.temporal.ChronoUnit;
import java.util.List;

/**
 * Scheduled tasks for proactive notifications.
 */
@Component
public class NotificationScheduler {

    private static final Logger logger = LoggerFactory.getLogger(NotificationScheduler.class);

    private final NotificationRepository notificationRepository;
    private final ApplicationEventPublisher eventPublisher;
    private final NotificationProperties properties;
    private final EntityManager entityManager;

    public NotificationScheduler(NotificationRepository notificationRepository,
                                  ApplicationEventPublisher eventPublisher,
                                  NotificationProperties properties,
                                  EntityManager entityManager) {
        this.notificationRepository = notificationRepository;
        this.eventPublisher = eventPublisher;
        this.properties = properties;
        this.entityManager = entityManager;
    }

    /**
     * Scan for expiring VCS tokens and publish events.
     * Runs daily at 6 AM.
     */
    @Scheduled(cron = "${codecrow.notification.token-scan-cron:0 0 6 * * *}")
    @Transactional(readOnly = true)
    public void scanExpiringTokens() {
        if (!properties.isTokenExpirationCheckEnabled()) {
            return;
        }

        logger.info("Starting VCS token expiration scan");
        int warningDays = properties.getTokenExpirationWarningDays();
        OffsetDateTime threshold = OffsetDateTime.now().plus(warningDays, ChronoUnit.DAYS);

        // Query VCS connections with expiring tokens
        String jpql = """
            SELECT vc.id, vc.workspace.id, vc.provider, vc.tokenExpiresAt
            FROM VcsConnection vc
            WHERE vc.tokenExpiresAt IS NOT NULL
            AND vc.tokenExpiresAt <= :threshold
            AND vc.tokenExpiresAt > :now
            """;

        Query query = entityManager.createQuery(jpql);
        query.setParameter("threshold", threshold);
        query.setParameter("now", OffsetDateTime.now());

        @SuppressWarnings("unchecked")
        List<Object[]> results = query.getResultList();

        logger.info("Found {} VCS connections with expiring tokens", results.size());

        for (Object[] row : results) {
            Long vcsConnectionId = (Long) row[0];
            Long workspaceId = (Long) row[1];
            String provider = (String) row[2];
            OffsetDateTime expiresAt = (OffsetDateTime) row[3];

            int daysUntilExpiry = (int) ChronoUnit.DAYS.between(OffsetDateTime.now(), expiresAt);

            eventPublisher.publishEvent(new TokenExpiringEvent(
                    this, workspaceId, vcsConnectionId, provider, expiresAt, daysUntilExpiry
            ));
        }

        // Also check for already expired tokens
        String expiredJpql = """
            SELECT vc.id, vc.workspace.id, vc.provider, vc.tokenExpiresAt
            FROM VcsConnection vc
            WHERE vc.tokenExpiresAt IS NOT NULL
            AND vc.tokenExpiresAt <= :now
            """;

        Query expiredQuery = entityManager.createQuery(expiredJpql);
        expiredQuery.setParameter("now", OffsetDateTime.now());

        @SuppressWarnings("unchecked")
        List<Object[]> expiredResults = expiredQuery.getResultList();

        logger.info("Found {} VCS connections with expired tokens", expiredResults.size());

        for (Object[] row : expiredResults) {
            Long vcsConnectionId = (Long) row[0];
            Long workspaceId = (Long) row[1];
            String provider = (String) row[2];
            OffsetDateTime expiresAt = (OffsetDateTime) row[3];

            eventPublisher.publishEvent(new TokenExpiringEvent(
                    this, workspaceId, vcsConnectionId, provider, expiresAt, 0
            ));
        }

        logger.info("VCS token expiration scan completed");
    }

    /**
     * Clean up expired and old read notifications.
     * Runs daily at 3 AM.
     */
    @Scheduled(cron = "${codecrow.notification.cleanup-cron:0 0 3 * * *}")
    @Transactional
    public void cleanupOldNotifications() {
        logger.info("Starting notification cleanup");

        // Delete expired notifications
        int expiredDeleted = notificationRepository.deleteExpiredNotifications(OffsetDateTime.now());
        logger.info("Deleted {} expired notifications", expiredDeleted);

        // Delete old read notifications (default: older than 30 days)
        int retentionDays = properties.getReadNotificationRetentionDays();
        OffsetDateTime cutoff = OffsetDateTime.now().minus(retentionDays, ChronoUnit.DAYS);
        int oldDeleted = notificationRepository.deleteOldReadNotifications(cutoff);
        logger.info("Deleted {} old read notifications", oldDeleted);

        logger.info("Notification cleanup completed");
    }
}
