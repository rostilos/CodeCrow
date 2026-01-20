<?php

declare(strict_types=1);

namespace App\Repository;

use App\Entity\User;
use PDO;

/**
 * Repository for User entity database operations.
 */
class UserRepository
{
    private PDO $pdo;

    public function __construct(PDO $pdo)
    {
        $this->pdo = $pdo;
    }

    public function findById(int $id): ?User
    {
        $stmt = $this->pdo->prepare('SELECT * FROM users WHERE id = :id');
        $stmt->execute(['id' => $id]);
        $row = $stmt->fetch(PDO::FETCH_ASSOC);

        return $row ? $this->hydrate($row) : null;
    }

    public function findByEmail(string $email): ?User
    {
        $stmt = $this->pdo->prepare('SELECT * FROM users WHERE email = :email');
        $stmt->execute(['email' => $email]);
        $row = $stmt->fetch(PDO::FETCH_ASSOC);

        return $row ? $this->hydrate($row) : null;
    }

    public function findAll(int $limit = 100, int $offset = 0): array
    {
        $stmt = $this->pdo->prepare('SELECT * FROM users ORDER BY created_at DESC LIMIT :limit OFFSET :offset');
        $stmt->bindValue(':limit', $limit, PDO::PARAM_INT);
        $stmt->bindValue(':offset', $offset, PDO::PARAM_INT);
        $stmt->execute();

        return array_map([$this, 'hydrate'], $stmt->fetchAll(PDO::FETCH_ASSOC));
    }

    public function findActiveUsers(): array
    {
        $stmt = $this->pdo->prepare('SELECT * FROM users WHERE is_active = 1 ORDER BY last_name, first_name');
        $stmt->execute();

        return array_map([$this, 'hydrate'], $stmt->fetchAll(PDO::FETCH_ASSOC));
    }

    public function save(User $user): User
    {
        if ($user->getId() === null) {
            return $this->insert($user);
        }
        return $this->update($user);
    }

    private function insert(User $user): User
    {
        $stmt = $this->pdo->prepare('
            INSERT INTO users (email, password_hash, first_name, last_name, is_active, created_at) 
            VALUES (:email, :password_hash, :first_name, :last_name, :is_active, :created_at)
        ');

        $stmt->execute([
            'email' => $user->getEmail(),
            'password_hash' => $user->getPasswordHash(),
            'first_name' => $user->getFirstName(),
            'last_name' => $user->getLastName(),
            'is_active' => $user->isActive() ? 1 : 0,
            'created_at' => $user->getCreatedAt()->format('Y-m-d H:i:s'),
        ]);

        $reflection = new \ReflectionClass($user);
        $idProperty = $reflection->getProperty('id');
        $idProperty->setAccessible(true);
        $idProperty->setValue($user, (int) $this->pdo->lastInsertId());

        return $user;
    }

    private function update(User $user): User
    {
        $stmt = $this->pdo->prepare('
            UPDATE users 
            SET email = :email, password_hash = :password_hash, first_name = :first_name, 
                last_name = :last_name, is_active = :is_active, last_login_at = :last_login_at
            WHERE id = :id
        ');

        $stmt->execute([
            'id' => $user->getId(),
            'email' => $user->getEmail(),
            'password_hash' => $user->getPasswordHash(),
            'first_name' => $user->getFirstName(),
            'last_name' => $user->getLastName(),
            'is_active' => $user->isActive() ? 1 : 0,
            'last_login_at' => $user->getLastLoginAt()?->format('Y-m-d H:i:s'),
        ]);

        return $user;
    }

    public function delete(int $id): bool
    {
        $stmt = $this->pdo->prepare('DELETE FROM users WHERE id = :id');
        return $stmt->execute(['id' => $id]);
    }

    public function countAll(): int
    {
        $stmt = $this->pdo->query('SELECT COUNT(*) FROM users');
        return (int) $stmt->fetchColumn();
    }

    private function hydrate(array $row): User
    {
        $user = new User($row['email'], $row['first_name'], $row['last_name']);

        $reflection = new \ReflectionClass($user);
        
        $idProperty = $reflection->getProperty('id');
        $idProperty->setAccessible(true);
        $idProperty->setValue($user, (int) $row['id']);

        $passwordProperty = $reflection->getProperty('passwordHash');
        $passwordProperty->setAccessible(true);
        $passwordProperty->setValue($user, $row['password_hash']);

        $activeProperty = $reflection->getProperty('isActive');
        $activeProperty->setAccessible(true);
        $activeProperty->setValue($user, (bool) $row['is_active']);

        $createdProperty = $reflection->getProperty('createdAt');
        $createdProperty->setAccessible(true);
        $createdProperty->setValue($user, new \DateTimeImmutable($row['created_at']));

        if ($row['last_login_at']) {
            $lastLoginProperty = $reflection->getProperty('lastLoginAt');
            $lastLoginProperty->setAccessible(true);
            $lastLoginProperty->setValue($user, new \DateTimeImmutable($row['last_login_at']));
        }

        return $user;
    }
}
