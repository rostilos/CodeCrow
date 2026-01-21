<?php

declare(strict_types=1);

namespace App\Controller;

use App\Service\NotificationService;

/**
 * Controller for notification endpoints.
 * 
 * Feature: Notifications
 */
class NotificationController
{
    private NotificationService $notificationService;

    public function __construct(NotificationService $notificationService)
    {
        $this->notificationService = $notificationService;
    }

    /**
     * GET /notifications
     * Get notifications for the authenticated user.
     */
    public function index(array $request): array
    {
        $userId = $request['_user_id'] ?? null;
        if (!$userId) {
            return $this->errorResponse('Unauthorized', 401);
        }

        $unreadOnly = filter_var($request['unread_only'] ?? false, FILTER_VALIDATE_BOOLEAN);
        $limit = min((int) ($request['limit'] ?? 50), 100);

        $notifications = $this->notificationService->getUserNotifications($userId, $unreadOnly, $limit);
        $unreadCount = $this->notificationService->getUnreadCount($userId);

        return $this->successResponse([
            'notifications' => $notifications,
            'unreadCount' => $unreadCount,
        ]);
    }

    /**
     * GET /notifications/unread-count
     * Get unread notification count.
     */
    public function unreadCount(array $request): array
    {
        $userId = $request['_user_id'] ?? null;
        if (!$userId) {
            return $this->errorResponse('Unauthorized', 401);
        }

        $count = $this->notificationService->getUnreadCount($userId);

        return $this->successResponse([
            'count' => $count,
        ]);
    }

    /**
     * POST /notifications/{id}/read
     * Mark a notification as read.
     */
    public function markAsRead(array $request): array
    {
        $userId = $request['_user_id'] ?? null;
        if (!$userId) {
            return $this->errorResponse('Unauthorized', 401);
        }

        $notificationId = $request['id'] ?? null;
        if (!$notificationId) {
            return $this->errorResponse('Notification ID is required', 400);
        }

        try {
            $notification = $this->notificationService->markAsRead((int) $notificationId, $userId);
            return $this->successResponse([
                'notification' => $notification->toArray(),
            ]);
        } catch (\InvalidArgumentException $e) {
            return $this->errorResponse($e->getMessage(), 404);
        }
    }

    /**
     * POST /notifications/mark-all-read
     * Mark all notifications as read.
     */
    public function markAllAsRead(array $request): array
    {
        $userId = $request['_user_id'] ?? null;
        if (!$userId) {
            return $this->errorResponse('Unauthorized', 401);
        }

        $count = $this->notificationService->markAllAsRead($userId);

        return $this->successResponse([
            'message' => 'All notifications marked as read',
            'count' => $count,
        ]);
    }

    /**
     * DELETE /notifications/{id}
     * Delete a notification.
     */
    public function delete(array $request): array
    {
        $userId = $request['_user_id'] ?? null;
        if (!$userId) {
            return $this->errorResponse('Unauthorized', 401);
        }

        $notificationId = $request['id'] ?? null;
        if (!$notificationId) {
            return $this->errorResponse('Notification ID is required', 400);
        }

        try {
            $this->notificationService->deleteNotification((int) $notificationId, $userId);
            return $this->successResponse([
                'message' => 'Notification deleted',
            ]);
        } catch (\InvalidArgumentException $e) {
            return $this->errorResponse($e->getMessage(), 404);
        }
    }

    /**
     * DELETE /notifications
     * Delete all notifications.
     */
    public function deleteAll(array $request): array
    {
        $userId = $request['_user_id'] ?? null;
        if (!$userId) {
            return $this->errorResponse('Unauthorized', 401);
        }

        $count = $this->notificationService->deleteAllNotifications($userId);

        return $this->successResponse([
            'message' => 'All notifications deleted',
            'count' => $count,
        ]);
    }

    /**
     * POST /notifications/test
     * Send a test notification (for development).
     */
    public function sendTest(array $request): array
    {
        $userId = $request['_user_id'] ?? null;
        if (!$userId) {
            return $this->errorResponse('Unauthorized', 401);
        }

        $type = $request['type'] ?? 'info';
        $title = $request['title'] ?? 'Test Notification';
        $message = $request['message'] ?? 'This is a test notification.';

        try {
            $notification = $this->notificationService->notify($userId, $type, $title, $message);
            return $this->successResponse([
                'notification' => $notification->toArray(),
            ], 201);
        } catch (\InvalidArgumentException $e) {
            return $this->errorResponse($e->getMessage(), 400);
        }
    }

    private function successResponse(array $data, int $status = 200): array
    {
        return [
            'status' => $status,
            'success' => true,
            'data' => $data,
        ];
    }

    private function errorResponse(string $message, int $status): array
    {
        return [
            'status' => $status,
            'success' => false,
            'error' => $message,
        ];
    }
}
