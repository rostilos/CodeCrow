<?php

declare(strict_types=1);

namespace App\Repository;

use App\Entity\User;
use PDO;

/**
 * Repository for User entity database operations.
 * 
 * Release 1.0: Added caching support for improved performance.
 */
class UserRepository
{
    private PDO $pdo;
    private array $cache = [];
    private int $cacheHits = 0;
    private int $cacheMisses = 0;
    private int $cacheTtl = 300; // 5 minutes

    public function __construct(PDO $pdo)
    {
        $this->pdo = $pdo;
    }

    public function findById(int $id): ?User
    {
        $cacheKey = "user_id_$id";
        
        if ($this->hasCache($cacheKey)) {
            $this->cacheHits++;
            return $this->getCache($cacheKey);
        }
        
        $this->cacheMisses++;
        $stmt = $this->pdo->prepare('SELECT * FROM users WHERE id = :id');
        $stmt->execute(['id' => $id]);
        $row = $stmt->fetch(PDO::FETCH_ASSOC);

        $user = $row ? $this->hydrate($row) : null;
        $this->setCache($cacheKey, $user);
        
        return $user;
    }

    public function findByEmail(string $email): ?User
    {
        $cacheKey = "user_email_" . md5($email);
        
        if ($this->hasCache($cacheKey)) {
            $this->cacheHits++;
            return $this->getCache($cacheKey);
        }
        
        $this->cacheMisses++;
        $stmt = $this->pdo->prepare('SELECT * FROM users WHERE email = :email');
        $stmt->execute(['email' => $email]);
        $row = $stmt->fetch(PDO::FETCH_ASSOC);

        $user = $row ? $this->hydrate($row) : null;
        $this->setCache($cacheKey, $user);
        
        return $user;
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
        $cacheKey = "active_users";
        
        if ($this->hasCache($cacheKey)) {
            $this->cacheHits++;
            return $this->getCache($cacheKey);
        }
        
        $this->cacheMisses++;
        $stmt = $this->pdo->prepare('SELECT * FROM users WHERE is_active = 1 ORDER BY last_name, first_name');
        $stmt->execute();

        $users = array_map([$this, 'hydrate'], $stmt->fetchAll(PDO::FETCH_ASSOC));
        $this->setCache($cacheKey, $users);
        
        return $users;
    }

    public function save(User $user): User
    {
        $result = $user->getId() === null ? $this->insert($user) : $this->update($user);
        
        // Invalidate cache on save
        $this->invalidateUserCache($result);
        
        return $result;
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
        $user = $this->findById($id);
        if ($user) {
            $this->invalidateUserCache($user);
        }
        
        $stmt = $this->pdo->prepare('DELETE FROM users WHERE id = :id');
        return $stmt->execute(['id' => $id]);
    }

    public function countAll(): int
    {
        $cacheKey = "users_count";
        
        if ($this->hasCache($cacheKey)) {
            return $this->getCache($cacheKey);
        }
        
        $stmt = $this->pdo->query('SELECT COUNT(*) FROM users');
        $count = (int) $stmt->fetchColumn();
        $this->setCache($cacheKey, $count);
        
        return $count;
    }

    // Cache management methods
    
    private function hasCache(string $key): bool
    {
        if (!isset($this->cache[$key])) {
            return false;
        }
        
        if ($this->cache[$key]['expires'] < time()) {
            unset($this->cache[$key]);
            return false;
        }
        
        return true;
    }
    
    private function getCache(string $key): mixed
    {
        return $this->cache[$key]['value'] ?? null;
    }
    
    private function setCache(string $key, mixed $value): void
    {
        $this->cache[$key] = [
            'value' => $value,
            'expires' => time() + $this->cacheTtl,
        ];
    }
    
    private function invalidateUserCache(User $user): void
    {
        unset($this->cache["user_id_{$user->getId()}"]);
        unset($this->cache["user_email_" . md5($user->getEmail())]);
        unset($this->cache["active_users"]);
        unset($this->cache["users_count"]);
    }
    
    public function clearCache(): void
    {
        $this->cache = [];
    }
    
    public function getCacheStats(): array
    {
        return [
            'hits' => $this->cacheHits,
            'misses' => $this->cacheMisses,
            'hitRate' => $this->cacheHits + $this->cacheMisses > 0 
                ? round($this->cacheHits / ($this->cacheHits + $this->cacheMisses) * 100, 2) 
                : 0,
            'size' => count($this->cache),
        ];
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
