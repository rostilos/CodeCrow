package org.rostilos.codecrow.notification.dto;

import java.util.List;

/**
 * Response DTO for paginated notifications.
 */
public record NotificationPageResponse(
        List<NotificationDTO> notifications,
        long totalElements,
        int totalPages,
        int currentPage,
        int pageSize,
        long unreadCount
) {
}
