<?php

declare(strict_types=1);

namespace App\Service;

use App\Entity\Notification;
use App\Entity\User;
use App\Repository\NotificationRepository;
use App\Repository\UserRepository;

/**
 * Service for notification business logic.
 * 
 * Feature: Notifications
 */
class NotificationService
{
    private NotificationRepository $notificationRepository;
    private UserRepository $userRepository;
    private ?MailService $mailService;

    public function __construct(
        NotificationRepository $notificationRepository,
        UserRepository $userRepository,
        ?MailService $mailService = null
    ) {
        $this->notificationRepository = $notificationRepository;
        $this->userRepository = $userRepository;
        $this->mailService = $mailService;
    }

    /**
     * Send a notification to a user.
     */
    public function notify(int $userId, string $type, string $title, string $message, ?array $data = null): Notification
    {
        $user = $this->userRepository->findById($userId);
        if ($user === null) {
            throw new \InvalidArgumentException('User not found');
        }

        // Create in-app notification
        $notification = new Notification($userId, $type, $title, $message);
        if ($data !== null) {
            $notification->setData($data);
        }
        
        $notification = $this->notificationRepository->save($notification);

        // Send email notification if user wants it
        $this->sendEmailIfEnabled($user, $notification);

        return $notification;
    }

    /**
     * Send notification to multiple users.
     */
    public function notifyMany(array $userIds, string $type, string $title, string $message, ?array $data = null): array
    {
        $notifications = [];
        foreach ($userIds as $userId) {
            try {
                $notifications[] = $this->notify($userId, $type, $title, $message, $data);
            } catch (\InvalidArgumentException $e) {
                // Skip invalid users
                continue;
            }
        }
        return $notifications;
    }

    /**
     * Send a system-wide notification to all users.
     */
    public function notifyAll(string $title, string $message, ?array $data = null): int
    {
        $users = $this->userRepository->findActiveUsers();
        $count = 0;
        
        foreach ($users as $user) {
            $notification = new Notification($user->getId(), Notification::TYPE_SYSTEM, $title, $message);
            if ($data !== null) {
                $notification->setData($data);
            }
            $this->notificationRepository->save($notification);
            $count++;
        }
        
        return $count;
    }

    /**
     * Get notifications for a user.
     */
    public function getUserNotifications(int $userId, bool $unreadOnly = false, int $limit = 50): array
    {
        $notifications = $unreadOnly
            ? $this->notificationRepository->findUnreadByUserId($userId, $limit)
            : $this->notificationRepository->findByUserId($userId, $limit);

        return array_map(fn(Notification $n) => $n->toArray(), $notifications);
    }

    /**
     * Get unread notification count for a user.
     */
    public function getUnreadCount(int $userId): int
    {
        return $this->notificationRepository->countUnreadByUserId($userId);
    }

    /**
     * Mark a notification as read.
     */
    public function markAsRead(int $notificationId, int $userId): Notification
    {
        $notification = $this->notificationRepository->findById($notificationId);
        
        if ($notification === null) {
            throw new \InvalidArgumentException('Notification not found');
        }
        
        if ($notification->getUserId() !== $userId) {
            throw new \InvalidArgumentException('Notification does not belong to user');
        }
        
        $notification->markAsRead();
        return $this->notificationRepository->save($notification);
    }

    /**
     * Mark all notifications as read for a user.
     */
    public function markAllAsRead(int $userId): int
    {
        return $this->notificationRepository->markAllAsReadByUserId($userId);
    }

    /**
     * Delete a notification.
     */
    public function deleteNotification(int $notificationId, int $userId): bool
    {
        $notification = $this->notificationRepository->findById($notificationId);
        
        if ($notification === null) {
            throw new \InvalidArgumentException('Notification not found');
        }
        
        if ($notification->getUserId() !== $userId) {
            throw new \InvalidArgumentException('Notification does not belong to user');
        }
        
        return $this->notificationRepository->delete($notificationId);
    }

    /**
     * Delete all notifications for a user.
     */
    public function deleteAllNotifications(int $userId): int
    {
        return $this->notificationRepository->deleteAllByUserId($userId);
    }

    /**
     * Delete old notifications.
     */
    public function purgeOldNotifications(int $daysOld = 30): int
    {
        $cutoff = new \DateTimeImmutable("-{$daysOld} days");
        return $this->notificationRepository->deleteOlderThan($cutoff);
    }

    // Notification type helpers

    /**
     * Send a security notification.
     */
    public function notifySecurity(int $userId, string $title, string $message, ?array $data = null): Notification
    {
        return $this->notify($userId, Notification::TYPE_WARNING, $title, $message, $data);
    }

    /**
     * Send a success notification.
     */
    public function notifySuccess(int $userId, string $title, string $message, ?array $data = null): Notification
    {
        return $this->notify($userId, Notification::TYPE_SUCCESS, $title, $message, $data);
    }

    /**
     * Send an error notification.
     */
    public function notifyError(int $userId, string $title, string $message, ?array $data = null): Notification
    {
        return $this->notify($userId, Notification::TYPE_ERROR, $title, $message, $data);
    }

    /**
     * Send email notification if enabled.
     */
    private function sendEmailIfEnabled(User $user, Notification $notification): void
    {
        if ($this->mailService === null) {
            return;
        }

        // Determine email category based on notification type
        $emailCategory = match ($notification->getType()) {
            Notification::TYPE_WARNING, Notification::TYPE_ERROR => 'security',
            Notification::TYPE_SYSTEM => 'system',
            default => 'account',
        };

        if ($user->isNotificationEnabled('email', $emailCategory)) {
            $this->mailService->send(
                $user->getEmail(),
                $notification->getTitle(),
                $notification->getMessage(),
                ['notification' => $notification->toArray()]
            );
        }
    }
}
