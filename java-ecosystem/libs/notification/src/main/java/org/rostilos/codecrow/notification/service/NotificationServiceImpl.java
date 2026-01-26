package org.rostilos.codecrow.notification.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.rostilos.codecrow.core.model.user.User;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.model.workspace.WorkspaceMember;
import org.rostilos.codecrow.core.persistence.repository.workspace.WorkspaceMemberRepository;
import org.rostilos.codecrow.email.service.EmailService;
import org.rostilos.codecrow.notification.model.Notification;
import org.rostilos.codecrow.notification.model.NotificationPreference;
import org.rostilos.codecrow.notification.model.NotificationPriority;
import org.rostilos.codecrow.notification.model.NotificationType;
import org.rostilos.codecrow.notification.repository.NotificationPreferenceRepository;
import org.rostilos.codecrow.notification.repository.NotificationRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.OffsetDateTime;
import java.time.temporal.ChronoUnit;
import java.util.*;
import java.util.stream.Collectors;

@Service
@Transactional
public class NotificationServiceImpl implements NotificationService {

    private static final Logger logger = LoggerFactory.getLogger(NotificationServiceImpl.class);
    
    private static final int DUPLICATE_CHECK_HOURS = 24;

    private final NotificationRepository notificationRepository;
    private final NotificationPreferenceRepository preferenceRepository;
    private final WorkspaceMemberRepository workspaceMemberRepository;
    private final EmailService emailService;
    private final ObjectMapper objectMapper;

    public NotificationServiceImpl(
            NotificationRepository notificationRepository,
            NotificationPreferenceRepository preferenceRepository,
            WorkspaceMemberRepository workspaceMemberRepository,
            EmailService emailService,
            ObjectMapper objectMapper) {
        this.notificationRepository = notificationRepository;
        this.preferenceRepository = preferenceRepository;
        this.workspaceMemberRepository = workspaceMemberRepository;
        this.emailService = emailService;
        this.objectMapper = objectMapper;
    }

    // ===== NOTIFICATION CREATION =====

    @Override
    public Notification createNotification(User user, NotificationType type, String title, String message) {
        return builder()
                .user(user)
                .type(type)
                .title(title)
                .message(message)
                .send();
    }

    @Override
    public Notification createNotification(User user, Workspace workspace, NotificationType type,
                                           String title, String message) {
        return builder()
                .user(user)
                .workspace(workspace)
                .type(type)
                .title(title)
                .message(message)
                .send();
    }

    @Override
    public Notification createNotification(NotificationBuilder builder) {
        return builder.send();
    }

    @Override
    public List<Notification> notifyWorkspaceMembers(Workspace workspace, NotificationType type,
                                                      String title, String message, String... roles) {
        Set<String> roleSet = new HashSet<>(Arrays.asList(roles));
        List<WorkspaceMember> members = workspaceMemberRepository.findByWorkspace_Id(workspace.getId());
        
        return members.stream()
                .filter(m -> roleSet.isEmpty() || roleSet.contains(m.getRole().name()))
                .map(member -> createNotification(member.getUser(), workspace, type, title, message))
                .collect(Collectors.toList());
    }

    @Override
    public List<Notification> notifyWorkspaceAdmins(Workspace workspace, NotificationType type,
                                                     String title, String message) {
        return notifyWorkspaceMembers(workspace, type, title, message, "OWNER", "ADMIN");
    }

    // ===== NOTIFICATION RETRIEVAL =====

    @Override
    @Transactional(readOnly = true)
    public Page<Notification> getUserNotifications(Long userId, Pageable pageable) {
        return notificationRepository.findByUserIdOrderByCreatedAtDesc(userId, pageable);
    }

    @Override
    @Transactional(readOnly = true)
    public Page<Notification> getUnreadNotifications(Long userId, Pageable pageable) {
        return notificationRepository.findByUserIdAndReadFalseOrderByCreatedAtDesc(userId, pageable);
    }

    @Override
    @Transactional(readOnly = true)
    public Page<Notification> getWorkspaceNotifications(Long userId, Long workspaceId, Pageable pageable) {
        return notificationRepository.findByUserIdAndWorkspaceIdOrderByCreatedAtDesc(userId, workspaceId, pageable);
    }

    @Override
    @Transactional(readOnly = true)
    public long getUnreadCount(Long userId) {
        return notificationRepository.countByUserIdAndReadFalse(userId);
    }

    @Override
    @Transactional(readOnly = true)
    public long getUnreadCount(Long userId, Long workspaceId) {
        return notificationRepository.countByUserIdAndWorkspaceIdAndReadFalse(userId, workspaceId);
    }

    @Override
    @Transactional(readOnly = true)
    public Optional<Notification> getNotification(Long notificationId, Long userId) {
        return notificationRepository.findById(notificationId)
                .filter(n -> n.getUser().getId().equals(userId));
    }

    // ===== NOTIFICATION MANAGEMENT =====

    @Override
    public void markAsRead(Long notificationId, Long userId) {
        notificationRepository.findById(notificationId)
                .filter(n -> n.getUser().getId().equals(userId))
                .ifPresent(n -> {
                    n.markAsRead();
                    notificationRepository.save(n);
                });
    }

    @Override
    public void markAsRead(List<Long> notificationIds, Long userId) {
        notificationIds.forEach(id -> markAsRead(id, userId));
    }

    @Override
    public void markAllAsRead(Long userId) {
        notificationRepository.markAllAsReadForUser(userId, OffsetDateTime.now());
    }

    @Override
    public void markAllAsRead(Long userId, Long workspaceId) {
        notificationRepository.markAllAsReadForUserInWorkspace(userId, workspaceId, OffsetDateTime.now());
    }

    @Override
    public void deleteNotification(Long notificationId, Long userId) {
        notificationRepository.findById(notificationId)
                .filter(n -> n.getUser().getId().equals(userId))
                .ifPresent(notificationRepository::delete);
    }

    // ===== PREFERENCE MANAGEMENT =====

    @Override
    @Transactional(readOnly = true)
    public List<NotificationPreference> getUserPreferences(Long userId) {
        return preferenceRepository.findGlobalPreferencesByUserId(userId);
    }

    @Override
    @Transactional(readOnly = true)
    public List<NotificationPreference> getWorkspacePreferences(Long userId, Long workspaceId) {
        return preferenceRepository.findByUserIdAndWorkspaceId(userId, workspaceId);
    }

    @Override
    public NotificationPreference updatePreference(Long userId, NotificationType type, Long workspaceId,
                                                   boolean inAppEnabled, boolean emailEnabled,
                                                   NotificationPriority minPriority) {
        NotificationPreference pref;
        
        if (workspaceId != null) {
            pref = preferenceRepository.findByUserIdAndTypeAndWorkspaceId(userId, type, workspaceId)
                    .orElseGet(() -> {
                        NotificationPreference newPref = new NotificationPreference();
                        newPref.setType(type);
                        return newPref;
                    });
        } else {
            pref = preferenceRepository.findGlobalPreference(userId, type)
                    .orElseGet(() -> {
                        NotificationPreference newPref = new NotificationPreference();
                        newPref.setType(type);
                        return newPref;
                    });
        }
        
        pref.setInAppEnabled(inAppEnabled);
        pref.setEmailEnabled(emailEnabled);
        pref.setMinPriority(minPriority);
        
        return preferenceRepository.save(pref);
    }

    @Override
    @Transactional(readOnly = true)
    public NotificationPreference getEffectivePreference(Long userId, NotificationType type, Long workspaceId) {
        if (workspaceId != null) {
            List<NotificationPreference> prefs = preferenceRepository.findEffectivePreference(userId, type, workspaceId);
            if (!prefs.isEmpty()) {
                return prefs.get(0);
            }
        }
        
        return preferenceRepository.findGlobalPreference(userId, type)
                .orElseGet(() -> createDefaultPreference(type));
    }

    @Override
    public void resetPreferences(Long userId) {
        List<NotificationPreference> prefs = preferenceRepository.findGlobalPreferencesByUserId(userId);
        preferenceRepository.deleteAll(prefs);
    }

    private NotificationPreference createDefaultPreference(NotificationType type) {
        NotificationPreference pref = new NotificationPreference();
        pref.setType(type);
        pref.setInAppEnabled(true);
        pref.setEmailEnabled(type.getDefaultPriority().getLevel() >= NotificationPriority.HIGH.getLevel());
        pref.setMinPriority(NotificationPriority.LOW);
        return pref;
    }

    // ===== BUILDER =====

    @Override
    public NotificationBuilder builder() {
        return new NotificationBuilderImpl();
    }

    private class NotificationBuilderImpl implements NotificationBuilder {
        private User user;
        private Workspace workspace;
        private NotificationType type;
        private NotificationPriority priority;
        private String title;
        private String message;
        private String actionUrl;
        private String actionLabel;
        private Map<String, Object> metadata = new HashMap<>();
        private Integer expiresInDays;
        private boolean skipDuplicateCheck = false;

        @Override
        public NotificationBuilder user(User user) {
            this.user = user;
            return this;
        }

        @Override
        public NotificationBuilder workspace(Workspace workspace) {
            this.workspace = workspace;
            return this;
        }

        @Override
        public NotificationBuilder type(NotificationType type) {
            this.type = type;
            return this;
        }

        @Override
        public NotificationBuilder priority(NotificationPriority priority) {
            this.priority = priority;
            return this;
        }

        @Override
        public NotificationBuilder title(String title) {
            this.title = title;
            return this;
        }

        @Override
        public NotificationBuilder message(String message) {
            this.message = message;
            return this;
        }

        @Override
        public NotificationBuilder actionUrl(String url) {
            this.actionUrl = url;
            return this;
        }

        @Override
        public NotificationBuilder actionLabel(String label) {
            this.actionLabel = label;
            return this;
        }

        @Override
        public NotificationBuilder metadata(Map<String, Object> metadata) {
            this.metadata = new HashMap<>(metadata);
            return this;
        }

        @Override
        public NotificationBuilder addMetadata(String key, Object value) {
            this.metadata.put(key, value);
            return this;
        }

        @Override
        public NotificationBuilder expiresInDays(int days) {
            this.expiresInDays = days;
            return this;
        }

        @Override
        public NotificationBuilder skipDuplicateCheck() {
            this.skipDuplicateCheck = true;
            return this;
        }

        @Override
        public Notification send() {
            Objects.requireNonNull(user, "User is required");
            Objects.requireNonNull(type, "Notification type is required");
            Objects.requireNonNull(title, "Title is required");
            Objects.requireNonNull(message, "Message is required");

            // Check user preferences
            Long workspaceId = workspace != null ? workspace.getId() : null;
            NotificationPreference pref = getEffectivePreference(user.getId(), type, workspaceId);
            NotificationPriority effectivePriority = priority != null ? priority : type.getDefaultPriority();

            // Skip if user disabled this notification type (unless critical)
            if (!pref.shouldNotify(effectivePriority, false) && effectivePriority != NotificationPriority.CRITICAL) {
                logger.debug("Skipping notification for user {} - disabled by preference", user.getId());
                return null;
            }

            // Check for duplicate notifications
            if (!skipDuplicateCheck && workspaceId != null) {
                OffsetDateTime checkAfter = OffsetDateTime.now().minus(DUPLICATE_CHECK_HOURS, ChronoUnit.HOURS);
                List<Notification> recent = notificationRepository.findRecentUnreadByUserTypeAndWorkspace(
                        user.getId(), type, workspaceId, checkAfter);
                if (!recent.isEmpty()) {
                    logger.debug("Skipping duplicate notification for user {} type {}", user.getId(), type);
                    return recent.get(0);
                }
            }

            // Create notification
            Notification notification = new Notification(user, workspace, type, title, message);
            notification.setPriority(effectivePriority);
            notification.setActionUrl(actionUrl);
            notification.setActionLabel(actionLabel);

            if (expiresInDays != null) {
                notification.setExpiresAt(OffsetDateTime.now().plusDays(expiresInDays));
            }

            if (!metadata.isEmpty()) {
                try {
                    notification.setMetadataJson(objectMapper.writeValueAsString(metadata));
                } catch (JsonProcessingException e) {
                    logger.warn("Failed to serialize notification metadata", e);
                }
            }

            notification = notificationRepository.save(notification);
            logger.info("Created notification {} for user {} type {}", notification.getId(), user.getId(), type);

            // Send email if enabled
            if (pref.shouldNotify(effectivePriority, true)) {
                sendEmailNotificationAsync(notification);
            }

            return notification;
        }
    }

    @Async
    protected void sendEmailNotificationAsync(Notification notification) {
        try {
            User user = notification.getUser();
            String subject = "[CodeCrow] " + notification.getTitle();
            
            // Build email content
            StringBuilder content = new StringBuilder();
            content.append(notification.getMessage());
            
            if (notification.getActionUrl() != null) {
                content.append("\n\n");
                content.append(notification.getActionLabel() != null ? notification.getActionLabel() : "View Details");
                content.append(": ").append(notification.getActionUrl());
            }
            
            emailService.sendSimpleEmail(user.getEmail(), subject, content.toString());
            
            notification.setEmailSent(true);
            notificationRepository.save(notification);
            
            logger.debug("Sent email notification {} to {}", notification.getId(), user.getEmail());
        } catch (Exception e) {
            logger.error("Failed to send email notification {}", notification.getId(), e);
        }
    }
}
