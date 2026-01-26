package org.rostilos.codecrow.webserver.notification.controller;

import jakarta.validation.Valid;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.workspace.WorkspaceRepository;
import org.rostilos.codecrow.notification.dto.*;
import org.rostilos.codecrow.notification.model.Notification;
import org.rostilos.codecrow.notification.model.NotificationPreference;
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

import java.util.List;
import java.util.Map;
import java.util.NoSuchElementException;
import java.util.stream.Collectors;

/**
 * REST controller for workspace-specific notification management.
 */
@CrossOrigin(origins = "*", maxAge = 3600)
@RestController
@RequestMapping("/api/{workspaceSlug}/notifications")
public class WorkspaceNotificationController {

    private static final int MAX_PAGE_SIZE = 50;

    private final NotificationService notificationService;
    private final WorkspaceRepository workspaceRepository;

    public WorkspaceNotificationController(NotificationService notificationService,
                                            WorkspaceRepository workspaceRepository) {
        this.notificationService = notificationService;
        this.workspaceRepository = workspaceRepository;
    }

    /**
     * Get notifications for the current user in a specific workspace.
     */
    @GetMapping
    @PreAuthorize("@workspaceSecurity.isMember(#workspaceSlug, authentication)")
    public ResponseEntity<NotificationPageResponse> getWorkspaceNotifications(
            @AuthenticationPrincipal UserDetailsImpl userDetails,
            @PathVariable String workspaceSlug,
            @RequestParam(defaultValue = "0") int page,
            @RequestParam(defaultValue = "20") int size) {

        Workspace workspace = workspaceRepository.findBySlug(workspaceSlug)
                .orElseThrow(() -> new NoSuchElementException("Workspace not found"));

        int pageSize = Math.min(size, MAX_PAGE_SIZE);
        Pageable pageable = PageRequest.of(page, pageSize);

        Page<Notification> notifications = notificationService.getWorkspaceNotifications(
                userDetails.getId(), workspace.getId(), pageable);

        long unreadCount = notificationService.getUnreadCount(userDetails.getId(), workspace.getId());

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
     * Get unread notification count for workspace.
     */
    @GetMapping("/unread-count")
    @PreAuthorize("@workspaceSecurity.isMember(#workspaceSlug, authentication)")
    public ResponseEntity<Map<String, Long>> getUnreadCount(
            @AuthenticationPrincipal UserDetailsImpl userDetails,
            @PathVariable String workspaceSlug) {

        Workspace workspace = workspaceRepository.findBySlug(workspaceSlug)
                .orElseThrow(() -> new NoSuchElementException("Workspace not found"));

        long count = notificationService.getUnreadCount(userDetails.getId(), workspace.getId());
        return ResponseEntity.ok(Map.of("count", count));
    }

    /**
     * Mark all workspace notifications as read.
     */
    @PostMapping("/mark-all-read")
    @PreAuthorize("@workspaceSecurity.isMember(#workspaceSlug, authentication)")
    public ResponseEntity<MessageResponse> markAllAsRead(
            @AuthenticationPrincipal UserDetailsImpl userDetails,
            @PathVariable String workspaceSlug) {

        Workspace workspace = workspaceRepository.findBySlug(workspaceSlug)
                .orElseThrow(() -> new NoSuchElementException("Workspace not found"));

        notificationService.markAllAsRead(userDetails.getId(), workspace.getId());
        return ResponseEntity.ok(new MessageResponse("All workspace notifications marked as read"));
    }

    /**
     * Get workspace-specific notification preferences.
     */
    @GetMapping("/preferences")
    @PreAuthorize("@workspaceSecurity.isMember(#workspaceSlug, authentication)")
    public ResponseEntity<List<NotificationPreferenceDTO>> getWorkspacePreferences(
            @AuthenticationPrincipal UserDetailsImpl userDetails,
            @PathVariable String workspaceSlug) {

        Workspace workspace = workspaceRepository.findBySlug(workspaceSlug)
                .orElseThrow(() -> new NoSuchElementException("Workspace not found"));

        List<NotificationPreference> prefs = notificationService.getWorkspacePreferences(
                userDetails.getId(), workspace.getId());

        List<NotificationPreferenceDTO> dtos = prefs.stream()
                .map(NotificationPreferenceDTO::fromEntity)
                .collect(Collectors.toList());

        return ResponseEntity.ok(dtos);
    }

    /**
     * Update a workspace-specific notification preference.
     */
    @PutMapping("/preferences")
    @PreAuthorize("@workspaceSecurity.isMember(#workspaceSlug, authentication)")
    public ResponseEntity<NotificationPreferenceDTO> updateWorkspacePreference(
            @AuthenticationPrincipal UserDetailsImpl userDetails,
            @PathVariable String workspaceSlug,
            @Valid @RequestBody UpdatePreferenceRequest request) {

        Workspace workspace = workspaceRepository.findBySlug(workspaceSlug)
                .orElseThrow(() -> new NoSuchElementException("Workspace not found"));

        NotificationPreference pref = notificationService.updatePreference(
                userDetails.getId(),
                request.type(),
                workspace.getId(),
                request.inAppEnabled(),
                request.emailEnabled(),
                request.minPriority()
        );

        return ResponseEntity.ok(NotificationPreferenceDTO.fromEntity(pref));
    }
}
