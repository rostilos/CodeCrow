package org.rostilos.codecrow.notification.listener;

import org.rostilos.codecrow.core.model.user.User;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.user.UserRepository;
import org.rostilos.codecrow.core.persistence.repository.workspace.WorkspaceRepository;
import org.rostilos.codecrow.events.analysis.AnalysisCompletedEvent;
import org.rostilos.codecrow.events.notification.*;
import org.rostilos.codecrow.notification.model.NotificationPriority;
import org.rostilos.codecrow.notification.model.NotificationType;
import org.rostilos.codecrow.notification.service.NotificationService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.event.EventListener;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.util.Optional;

/**
 * Event listener that converts application events into user notifications.
 */
@Component
public class NotificationEventListener {

    private static final Logger logger = LoggerFactory.getLogger(NotificationEventListener.class);

    private final NotificationService notificationService;
    private final WorkspaceRepository workspaceRepository;
    private final UserRepository userRepository;

    public NotificationEventListener(NotificationService notificationService,
                                     WorkspaceRepository workspaceRepository,
                                     UserRepository userRepository) {
        this.notificationService = notificationService;
        this.workspaceRepository = workspaceRepository;
        this.userRepository = userRepository;
    }

    @EventListener
    @Async
    @Transactional
    public void handleTokenExpiringEvent(TokenExpiringEvent event) {
        logger.info("Handling token expiring event for workspace {}", event.getWorkspaceId());

        Optional<Workspace> workspaceOpt = workspaceRepository.findById(event.getWorkspaceId());
        if (workspaceOpt.isEmpty()) {
            logger.warn("Workspace {} not found for token expiring event", event.getWorkspaceId());
            return;
        }

        Workspace workspace = workspaceOpt.get();
        NotificationType type = event.isExpired() ? NotificationType.TOKEN_EXPIRED : NotificationType.TOKEN_EXPIRING;
        
        String title = event.isExpired() 
                ? "VCS Connection Expired" 
                : "VCS Connection Expiring Soon";
        
        String message = event.isExpired()
                ? String.format("Your %s connection has expired. Please reconnect to continue using CodeCrow.", 
                        event.getVcsProvider())
                : String.format("Your %s connection will expire in %d days. Please reconnect to avoid interruption.", 
                        event.getVcsProvider(), event.getDaysUntilExpiry());

        notificationService.notifyWorkspaceAdmins(workspace, type, title, message);
    }

    @EventListener
    @Async
    @Transactional
    public void handleWorkspaceOwnershipTransferEvent(WorkspaceOwnershipTransferEvent event) {
        logger.info("Handling workspace ownership transfer event for workspace {}", event.getWorkspaceId());

        Optional<Workspace> workspaceOpt = workspaceRepository.findById(event.getWorkspaceId());
        if (workspaceOpt.isEmpty()) {
            return;
        }

        Workspace workspace = workspaceOpt.get();

        // Notify the old owner
        userRepository.findById(event.getFromUserId()).ifPresent(fromUser -> {
            notificationService.builder()
                    .user(fromUser)
                    .workspace(workspace)
                    .type(NotificationType.WORKSPACE_OWNERSHIP_TRANSFER)
                    .title("Workspace Ownership Transferred")
                    .message(String.format("You have transferred ownership of workspace '%s' to %s.",
                            event.getWorkspaceName(), event.getToUserEmail()))
                    .send();
        });

        // Notify the new owner
        userRepository.findById(event.getToUserId()).ifPresent(toUser -> {
            notificationService.builder()
                    .user(toUser)
                    .workspace(workspace)
                    .type(NotificationType.WORKSPACE_OWNERSHIP_TRANSFER)
                    .priority(NotificationPriority.HIGH)
                    .title("You Are Now Workspace Owner")
                    .message(String.format("You are now the owner of workspace '%s'. " +
                            "You have full control over workspace settings, billing, and member management.",
                            event.getWorkspaceName()))
                    .actionUrl(String.format("/workspace/%s/settings", workspace.getSlug()))
                    .actionLabel("View Workspace Settings")
                    .send();
        });
    }

    @EventListener
    @Async
    @Transactional
    public void handleBillingNotificationEvent(BillingNotificationEvent event) {
        logger.info("Handling billing notification event for workspace {}", event.getWorkspaceId());

        Optional<Workspace> workspaceOpt = workspaceRepository.findById(event.getWorkspaceId());
        if (workspaceOpt.isEmpty()) {
            return;
        }

        Workspace workspace = workspaceOpt.get();
        
        String title = switch (event.getBillingEventType()) {
            case PAYMENT_DUE -> "Payment Due";
            case PAYMENT_FAILED -> "Payment Failed";
            case PAYMENT_SUCCESS -> "Payment Successful";
            case PLAN_CHANGED -> "Plan Changed";
            case PLAN_EXPIRED -> "Plan Expired";
            case TRIAL_ENDING -> "Trial Ending Soon";
            case INVOICE_GENERATED -> "New Invoice Available";
        };

        NotificationPriority priority = switch (event.getBillingEventType()) {
            case PAYMENT_FAILED, PLAN_EXPIRED -> NotificationPriority.CRITICAL;
            case PAYMENT_DUE, TRIAL_ENDING -> NotificationPriority.HIGH;
            default -> NotificationPriority.MEDIUM;
        };

        var builder = notificationService.builder()
                .workspace(workspace)
                .type(NotificationType.BILLING_ALERT)
                .priority(priority)
                .title(title)
                .message(event.getMessage())
                .addMetadata("billingEventType", event.getBillingEventType().name())
                .addMetadata("planName", event.getPlanName());

        if (event.getAmount() != null) {
            builder.addMetadata("amount", event.getAmount().toString());
            builder.addMetadata("currency", event.getCurrency());
        }

        if (event.getInvoiceUrl() != null) {
            builder.actionUrl(event.getInvoiceUrl())
                   .actionLabel("View Invoice");
        }

        // Send to all admins/owners
        notificationService.notifyWorkspaceAdmins(workspace, NotificationType.BILLING_ALERT, title, event.getMessage());
    }

    @EventListener
    @Async
    @Transactional
    public void handleQuotaWarningEvent(QuotaWarningEvent event) {
        logger.info("Handling quota warning event for workspace {}", event.getWorkspaceId());

        Optional<Workspace> workspaceOpt = workspaceRepository.findById(event.getWorkspaceId());
        if (workspaceOpt.isEmpty()) {
            return;
        }

        Workspace workspace = workspaceOpt.get();
        NotificationType type = event.isExceeded() ? NotificationType.QUOTA_EXCEEDED : NotificationType.QUOTA_WARNING;
        
        String quotaName = switch (event.getQuotaType()) {
            case ANALYSES_PER_MONTH -> "monthly analysis";
            case STORAGE -> "storage";
            case PROJECTS -> "project";
            case MEMBERS -> "member";
        };

        String title = event.isExceeded()
                ? String.format("%s Quota Exceeded", capitalize(quotaName))
                : String.format("%s Quota Warning", capitalize(quotaName));

        String message = event.isExceeded()
                ? String.format("Your workspace has exceeded its %s quota (%d/%d). " +
                        "Please upgrade your plan to continue.", quotaName, event.getCurrentUsage(), event.getQuotaLimit())
                : String.format("Your workspace is approaching its %s quota (%d%% used). " +
                        "Consider upgrading your plan.", quotaName, event.getUsagePercentage());

        notificationService.notifyWorkspaceAdmins(workspace, type, title, message);
    }

    @EventListener
    @Async
    @Transactional
    public void handleSystemNotificationEvent(SystemNotificationEvent event) {
        logger.info("Handling system notification event: {}", event.getTitle());

        NotificationType type = switch (event.getSystemEventType()) {
            case ANNOUNCEMENT -> NotificationType.SYSTEM_ANNOUNCEMENT;
            case PRODUCT_UPDATE -> NotificationType.PRODUCT_UPDATE;
            case SECURITY_ADVISORY -> NotificationType.SECURITY_ALERT;
        };

        if (event.isBroadcastToAll()) {
            // Broadcast to all users - this should be batched for large user bases
            logger.info("Broadcasting system notification to all users");
            // Note: In production, this should use pagination/batching
            userRepository.findAll().forEach(user -> {
                notificationService.builder()
                        .user(user)
                        .type(type)
                        .title(event.getTitle())
                        .message(event.getMessage())
                        .actionUrl(event.getActionUrl())
                        .actionLabel(event.getActionLabel())
                        .skipDuplicateCheck()
                        .send();
            });
        } else if (event.getTargetUserIds() != null && !event.getTargetUserIds().isEmpty()) {
            // Send to specific users
            event.getTargetUserIds().forEach(userId -> {
                userRepository.findById(userId).ifPresent(user -> {
                    notificationService.builder()
                            .user(user)
                            .type(type)
                            .title(event.getTitle())
                            .message(event.getMessage())
                            .actionUrl(event.getActionUrl())
                            .actionLabel(event.getActionLabel())
                            .send();
                });
            });
        }
    }

    @EventListener
    @Async
    @Transactional
    public void handleAnalysisCompletedEvent(AnalysisCompletedEvent event) {
        // Only notify on failures - success notifications can be too noisy
        if (event.getStatus() == AnalysisCompletedEvent.CompletionStatus.FAILED) {
            logger.info("Handling analysis failed event for project {}", event.getProjectId());
            
            // We'd need to fetch the project and workspace here
            // For now, log and skip - this would need project repository access
            logger.debug("Analysis failed notification would be sent for job {}", event.getJobId());
        }
    }

    private String capitalize(String str) {
        if (str == null || str.isEmpty()) {
            return str;
        }
        return str.substring(0, 1).toUpperCase() + str.substring(1);
    }
}
