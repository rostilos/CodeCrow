package org.rostilos.codecrow.webserver.notification.controller;

import jakarta.validation.Valid;
import org.rostilos.codecrow.notification.dto.*;
import org.rostilos.codecrow.notification.model.Notification;
import org.rostilos.codecrow.notification.model.NotificationPreference;
import org.rostilos.codecrow.notification.model.NotificationPriority;
import org.rostilos.codecrow.notification.model.NotificationType;
import org.rostilos.codecrow.notification.service.NotificationService;
import org.rostilos.codecrow.security.service.UserDetailsImpl;
import org.rostilos.codecrow.webserver.generic.dto.message.MessageResponse;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Pageable;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;

import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

/**
 * REST controller for user notification management.
 */
@CrossOrigin(origins = "*", maxAge = 3600)
@RestController
@RequestMapping("/api/notifications")
public class NotificationController {

    private static final int DEFAULT_PAGE_SIZE = 20;
    private static final int MAX_PAGE_SIZE = 50;

    private final NotificationService notificationService;

    public NotificationController(NotificationService notificationService) {
        this.notificationService = notificationService;
    }

    /**
     * Get all notifications for the current user.
     */
    @GetMapping
    @PreAuthorize("isAuthenticated()")
    public ResponseEntity<NotificationPageResponse> getNotifications(
            @AuthenticationPrincipal UserDetailsImpl userDetails,
            @RequestParam(defaultValue = "0") int page,
            @RequestParam(defaultValue = "20") int size,
            @RequestParam(required = false) Boolean unreadOnly) {

        int pageSize = Math.min(size, MAX_PAGE_SIZE);
        Pageable pageable = PageRequest.of(page, pageSize);

        Page<Notification> notifications;
        if (Boolean.TRUE.equals(unreadOnly)) {
            notifications = notificationService.getUnreadNotifications(userDetails.getId(), pageable);
        } else {
            notifications = notificationService.getUserNotifications(userDetails.getId(), pageable);
        }

        long unreadCount = notificationService.getUnreadCount(userDetails.getId());

        List<NotificationDTO> dtos = notifications.getContent().stream()
                .map(NotificationDTO::fromEntity)
                .collect(Collectors.toList());

        return ResponseEntity.ok(new NotificationPageResponse(
                dtos,
                notifications.getTotalElements(),
                notifications.getTotalPages(),
                page,
                pageSize,
                unreadCount
        ));
    }

    /**
     * Get unread notification count.
     */
    @GetMapping("/unread-count")
    @PreAuthorize("isAuthenticated()")
    public ResponseEntity<Map<String, Long>> getUnreadCount(
            @AuthenticationPrincipal UserDetailsImpl userDetails) {
        long count = notificationService.getUnreadCount(userDetails.getId());
        return ResponseEntity.ok(Map.of("count", count));
    }

    /**
     * Get a specific notification.
     */
    @GetMapping("/{notificationId}")
    @PreAuthorize("isAuthenticated()")
    public ResponseEntity<NotificationDTO> getNotification(
            @AuthenticationPrincipal UserDetailsImpl userDetails,
            @PathVariable Long notificationId) {

        return notificationService.getNotification(notificationId, userDetails.getId())
                .map(NotificationDTO::fromEntity)
                .map(ResponseEntity::ok)
                .orElse(ResponseEntity.notFound().build());
    }

    /**
     * Mark a single notification as read.
     */
    @PatchMapping("/{notificationId}/read")
    @PreAuthorize("isAuthenticated()")
    public ResponseEntity<MessageResponse> markAsRead(
            @AuthenticationPrincipal UserDetailsImpl userDetails,
            @PathVariable Long notificationId) {

        notificationService.markAsRead(notificationId, userDetails.getId());
        return ResponseEntity.ok(new MessageResponse("Notification marked as read"));
    }

    /**
     * Mark multiple notifications as read.
     */
    @PostMapping("/mark-read")
    @PreAuthorize("isAuthenticated()")
    public ResponseEntity<MessageResponse> markMultipleAsRead(
            @AuthenticationPrincipal UserDetailsImpl userDetails,
            @Valid @RequestBody MarkReadRequest request) {

        notificationService.markAsRead(request.notificationIds(), userDetails.getId());
        return ResponseEntity.ok(new MessageResponse("Notifications marked as read"));
    }

    /**
     * Mark all notifications as read.
     */
    @PostMapping("/mark-all-read")
    @PreAuthorize("isAuthenticated()")
    public ResponseEntity<MessageResponse> markAllAsRead(
            @AuthenticationPrincipal UserDetailsImpl userDetails) {

        notificationService.markAllAsRead(userDetails.getId());
        return ResponseEntity.ok(new MessageResponse("All notifications marked as read"));
    }

    /**
     * Delete a notification.
     */
    @DeleteMapping("/{notificationId}")
    @PreAuthorize("isAuthenticated()")
    public ResponseEntity<MessageResponse> deleteNotification(
            @AuthenticationPrincipal UserDetailsImpl userDetails,
            @PathVariable Long notificationId) {

        notificationService.deleteNotification(notificationId, userDetails.getId());
        return ResponseEntity.ok(new MessageResponse("Notification deleted"));
    }

    /**
     * Get user's notification preferences.
     */
    @GetMapping("/preferences")
    @PreAuthorize("isAuthenticated()")
    public ResponseEntity<List<NotificationPreferenceDTO>> getPreferences(
            @AuthenticationPrincipal UserDetailsImpl userDetails) {

        List<NotificationPreference> prefs = notificationService.getUserPreferences(userDetails.getId());
        
        // Fill in defaults for types without explicit preferences
        List<NotificationPreferenceDTO> dtos = Arrays.stream(NotificationType.values())
                .map(type -> prefs.stream()
                        .filter(p -> p.getType() == type && p.isGlobal())
                        .findFirst()
                        .map(NotificationPreferenceDTO::fromEntity)
                        .orElse(new NotificationPreferenceDTO(
                                null, type, type.getDisplayName(),
                                true, 
                                type.getDefaultPriority().getLevel() >= NotificationPriority.HIGH.getLevel(),
                                NotificationPriority.LOW, null, null)))
                .collect(Collectors.toList());

        return ResponseEntity.ok(dtos);
    }

    /**
     * Update a notification preference.
     */
    @PutMapping("/preferences")
    @PreAuthorize("isAuthenticated()")
    public ResponseEntity<NotificationPreferenceDTO> updatePreference(
            @AuthenticationPrincipal UserDetailsImpl userDetails,
            @Valid @RequestBody UpdatePreferenceRequest request) {

        NotificationPreference pref = notificationService.updatePreference(
                userDetails.getId(),
                request.type(),
                request.workspaceId(),
                request.inAppEnabled(),
                request.emailEnabled(),
                request.minPriority() != null ? request.minPriority() : NotificationPriority.LOW
        );

        return ResponseEntity.ok(NotificationPreferenceDTO.fromEntity(pref));
    }

    /**
     * Reset all preferences to defaults.
     */
    @DeleteMapping("/preferences")
    @PreAuthorize("isAuthenticated()")
    public ResponseEntity<MessageResponse> resetPreferences(
            @AuthenticationPrincipal UserDetailsImpl userDetails) {

        notificationService.resetPreferences(userDetails.getId());
        return ResponseEntity.ok(new MessageResponse("Preferences reset to defaults"));
    }

    /**
     * Get available notification types (for UI).
     */
    @GetMapping("/types")
    @PreAuthorize("isAuthenticated()")
    public ResponseEntity<List<Map<String, Object>>> getNotificationTypes() {
        List<Map<String, Object>> types = Arrays.stream(NotificationType.values())
                .map(type -> Map.<String, Object>of(
                        "type", type.name(),
                        "displayName", type.getDisplayName(),
                        "scope", type.getScope().name(),
                        "defaultPriority", type.getDefaultPriority().name()
                ))
                .collect(Collectors.toList());

        return ResponseEntity.ok(types);
    }
}
