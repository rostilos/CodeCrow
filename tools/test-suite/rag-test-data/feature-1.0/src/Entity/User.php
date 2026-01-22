<?php

declare(strict_types=1);

namespace App\Entity;

/**
 * User entity representing a system user.
 * 
 * Feature: User Roles - Added role support.
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
    
    // Feature: User Roles
    private array $roles = [];

    public function __construct(string $email, string $firstName, string $lastName)
    {
        $this->email = $email;
        $this->firstName = $firstName;
        $this->lastName = $lastName;
        $this->createdAt = new \DateTimeImmutable();
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
    
    // Role management methods (Feature: User Roles)
    
    /**
     * Get all roles assigned to this user.
     * 
     * @return Role[]
     */
    public function getRoles(): array
    {
        return $this->roles;
    }
    
    /**
     * Set the roles for this user.
     * 
     * @param Role[] $roles
     */
    public function setRoles(array $roles): self
    {
        $this->roles = $roles;
        return $this;
    }
    
    /**
     * Add a role to this user.
     */
    public function addRole(Role $role): self
    {
        foreach ($this->roles as $existingRole) {
            if ($existingRole->getId() === $role->getId()) {
                return $this; // Already has this role
            }
        }
        $this->roles[] = $role;
        return $this;
    }
    
    /**
     * Remove a role from this user.
     */
    public function removeRole(Role $role): self
    {
        $this->roles = array_filter(
            $this->roles,
            fn($r) => $r->getId() !== $role->getId()
        );
        return $this;
    }
    
    /**
     * Check if user has a specific role.
     */
    public function hasRole(string $roleSlug): bool
    {
        foreach ($this->roles as $role) {
            if ($role->getSlug() === $roleSlug) {
                return true;
            }
        }
        return false;
    }
    
    /**
     * Check if user has a specific permission through any of their roles.
     */
    public function hasPermission(string $permission): bool
    {
        foreach ($this->roles as $role) {
            if ($role->hasPermission($permission)) {
                return true;
            }
        }
        return false;
    }
    
    /**
     * Get all permissions this user has through their roles.
     */
    public function getAllPermissions(): array
    {
        $permissions = [];
        foreach ($this->roles as $role) {
            $permissions = array_merge($permissions, $role->getPermissions());
        }
        return array_unique($permissions);
    }
    
    /**
     * Check if user is an administrator.
     */
    public function isAdmin(): bool
    {
        return $this->hasRole('admin');
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
            'roles' => array_map(fn(Role $r) => $r->toArray(), $this->roles),
            'permissions' => $this->getAllPermissions(),
        ];
    }
}
