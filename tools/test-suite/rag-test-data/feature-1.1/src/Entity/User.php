<?php

declare(strict_types=1);

namespace App\Entity;

/**
 * User entity representing a system user.
 * 
 * Feature: Notifications - Added notification preferences.
 */
class User
{
    private ?int $id = null;
    private string $email;
    private string $passwordHash;
    private string $firstName;
    private string $lastName;
    private bool $isActive = true;
    private \DateTimeImmutable $createdAt;
    private ?\DateTimeImmutable $lastLoginAt = null;
    
    // Feature: Notifications
    private array $notificationPreferences = [];

    public function __construct(string $email, string $firstName, string $lastName)
    {
        $this->email = $email;
        $this->firstName = $firstName;
        $this->lastName = $lastName;
        $this->createdAt = new \DateTimeImmutable();
        $this->notificationPreferences = self::getDefaultNotificationPreferences();
    }

    public function getId(): ?int
    {
        return $this->id;
    }

    public function getEmail(): string
    {
        return $this->email;
    }

    public function setEmail(string $email): self
    {
        $this->email = $email;
        return $this;
    }

    public function getPasswordHash(): string
    {
        return $this->passwordHash;
    }

    public function setPassword(string $plainPassword): self
    {
        $this->passwordHash = password_hash($plainPassword, PASSWORD_ARGON2ID);
        return $this;
    }

    public function verifyPassword(string $plainPassword): bool
    {
        return password_verify($plainPassword, $this->passwordHash);
    }

    public function getFirstName(): string
    {
        return $this->firstName;
    }

    public function setFirstName(string $firstName): self
    {
        $this->firstName = $firstName;
        return $this;
    }

    public function getLastName(): string
    {
        return $this->lastName;
    }

    public function setLastName(string $lastName): self
    {
        $this->lastName = $lastName;
        return $this;
    }

    public function getFullName(): string
    {
        return $this->firstName . ' ' . $this->lastName;
    }

    public function isActive(): bool
    {
        return $this->isActive;
    }

    public function setActive(bool $isActive): self
    {
        $this->isActive = $isActive;
        return $this;
    }

    public function getCreatedAt(): \DateTimeImmutable
    {
        return $this->createdAt;
    }

    public function getLastLoginAt(): ?\DateTimeImmutable
    {
        return $this->lastLoginAt;
    }

    public function recordLogin(): self
    {
        $this->lastLoginAt = new \DateTimeImmutable();
        return $this;
    }
    
    // Notification preference methods (Feature: Notifications)
    
    /**
     * Get notification preferences.
     */
    public function getNotificationPreferences(): array
    {
        return $this->notificationPreferences;
    }
    
    /**
     * Set notification preferences.
     */
    public function setNotificationPreferences(array $preferences): self
    {
        $this->notificationPreferences = array_merge(
            self::getDefaultNotificationPreferences(),
            $preferences
        );
        return $this;
    }
    
    /**
     * Check if a specific notification type is enabled.
     */
    public function isNotificationEnabled(string $channel, string $type): bool
    {
        return $this->notificationPreferences[$channel][$type] ?? false;
    }
    
    /**
     * Enable a notification type.
     */
    public function enableNotification(string $channel, string $type): self
    {
        if (!isset($this->notificationPreferences[$channel])) {
            $this->notificationPreferences[$channel] = [];
        }
        $this->notificationPreferences[$channel][$type] = true;
        return $this;
    }
    
    /**
     * Disable a notification type.
     */
    public function disableNotification(string $channel, string $type): self
    {
        if (isset($this->notificationPreferences[$channel])) {
            $this->notificationPreferences[$channel][$type] = false;
        }
        return $this;
    }
    
    /**
     * Check if user wants email notifications.
     */
    public function wantsEmailNotifications(): bool
    {
        $emailPrefs = $this->notificationPreferences['email'] ?? [];
        return array_reduce($emailPrefs, fn($carry, $enabled) => $carry || $enabled, false);
    }
    
    /**
     * Check if user wants in-app notifications.
     */
    public function wantsInAppNotifications(): bool
    {
        $inAppPrefs = $this->notificationPreferences['in_app'] ?? [];
        return array_reduce($inAppPrefs, fn($carry, $enabled) => $carry || $enabled, false);
    }
    
    /**
     * Get default notification preferences.
     */
    public static function getDefaultNotificationPreferences(): array
    {
        return [
            'email' => [
                'security' => true,      // Password changes, suspicious activity
                'account' => true,       // Account status changes
                'system' => false,       // System announcements
                'marketing' => false,    // Promotional emails
            ],
            'in_app' => [
                'security' => true,
                'account' => true,
                'system' => true,
                'mentions' => true,
            ],
            'push' => [
                'security' => true,
                'urgent' => true,
            ],
        ];
    }

    public function toArray(): array
    {
        return [
            'id' => $this->id,
            'email' => $this->email,
            'firstName' => $this->firstName,
            'lastName' => $this->lastName,
            'fullName' => $this->getFullName(),
            'isActive' => $this->isActive,
            'createdAt' => $this->createdAt->format('c'),
            'lastLoginAt' => $this->lastLoginAt?->format('c'),
            'notificationPreferences' => $this->notificationPreferences,
        ];
    }
}
