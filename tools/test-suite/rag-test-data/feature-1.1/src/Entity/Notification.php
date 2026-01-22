<?php

declare(strict_types=1);

namespace App\Entity;

/**
 * Notification entity representing a user notification.
 * 
 * Feature: Notifications
 */
class Notification
{
    private ?int $id = null;
    private int $userId;
    private string $type;
    private string $title;
    private string $message;
    private ?array $data = null;
    private bool $isRead = false;
    private \DateTimeImmutable $createdAt;
    private ?\DateTimeImmutable $readAt = null;

    // Notification types
    public const TYPE_INFO = 'info';
    public const TYPE_SUCCESS = 'success';
    public const TYPE_WARNING = 'warning';
    public const TYPE_ERROR = 'error';
    public const TYPE_SYSTEM = 'system';

    public function __construct(int $userId, string $type, string $title, string $message)
    {
        $this->userId = $userId;
        $this->type = $type;
        $this->title = $title;
        $this->message = $message;
        $this->createdAt = new \DateTimeImmutable();
    }

    public function getId(): ?int
    {
        return $this->id;
    }

    public function getUserId(): int
    {
        return $this->userId;
    }

    public function getType(): string
    {
        return $this->type;
    }

    public function setType(string $type): self
    {
        if (!in_array($type, self::getValidTypes(), true)) {
            throw new \InvalidArgumentException("Invalid notification type: {$type}");
        }
        $this->type = $type;
        return $this;
    }

    public function getTitle(): string
    {
        return $this->title;
    }

    public function setTitle(string $title): self
    {
        $this->title = $title;
        return $this;
    }

    public function getMessage(): string
    {
        return $this->message;
    }

    public function setMessage(string $message): self
    {
        $this->message = $message;
        return $this;
    }

    public function getData(): ?array
    {
        return $this->data;
    }

    public function setData(?array $data): self
    {
        $this->data = $data;
        return $this;
    }

    public function isRead(): bool
    {
        return $this->isRead;
    }

    public function markAsRead(): self
    {
        if (!$this->isRead) {
            $this->isRead = true;
            $this->readAt = new \DateTimeImmutable();
        }
        return $this;
    }

    public function markAsUnread(): self
    {
        $this->isRead = false;
        $this->readAt = null;
        return $this;
    }

    public function getCreatedAt(): \DateTimeImmutable
    {
        return $this->createdAt;
    }

    public function getReadAt(): ?\DateTimeImmutable
    {
        return $this->readAt;
    }

    public function toArray(): array
    {
        return [
            'id' => $this->id,
            'userId' => $this->userId,
            'type' => $this->type,
            'title' => $this->title,
            'message' => $this->message,
            'data' => $this->data,
            'isRead' => $this->isRead,
            'createdAt' => $this->createdAt->format('c'),
            'readAt' => $this->readAt?->format('c'),
        ];
    }

    public static function getValidTypes(): array
    {
        return [
            self::TYPE_INFO,
            self::TYPE_SUCCESS,
            self::TYPE_WARNING,
            self::TYPE_ERROR,
            self::TYPE_SYSTEM,
        ];
    }

    /**
     * Create a welcome notification for new users.
     */
    public static function createWelcome(int $userId, string $userName): self
    {
        return new self(
            $userId,
            self::TYPE_SUCCESS,
            'Welcome!',
            "Welcome to CodeCrow, {$userName}! Get started by exploring your dashboard."
        );
    }

    /**
     * Create a password change notification.
     */
    public static function createPasswordChanged(int $userId): self
    {
        return new self(
            $userId,
            self::TYPE_WARNING,
            'Password Changed',
            'Your password was recently changed. If you did not make this change, please contact support immediately.'
        );
    }
}
