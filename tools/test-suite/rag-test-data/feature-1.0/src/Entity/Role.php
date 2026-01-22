<?php

declare(strict_types=1);

namespace App\Entity;

/**
 * Role entity representing a user role for RBAC.
 * 
 * Feature: User Roles
 */
class Role
{
    private ?int $id = null;
    private string $name;
    private string $slug;
    private ?string $description = null;
    private array $permissions = [];
    private \DateTimeImmutable $createdAt;

    public function __construct(string $name, string $slug)
    {
        $this->name = $name;
        $this->slug = $slug;
        $this->createdAt = new \DateTimeImmutable();
    }

    public function getId(): ?int
    {
        return $this->id;
    }

    public function getName(): string
    {
        return $this->name;
    }

    public function setName(string $name): self
    {
        $this->name = $name;
        return $this;
    }

    public function getSlug(): string
    {
        return $this->slug;
    }

    public function setSlug(string $slug): self
    {
        $this->slug = $slug;
        return $this;
    }

    public function getDescription(): ?string
    {
        return $this->description;
    }

    public function setDescription(?string $description): self
    {
        $this->description = $description;
        return $this;
    }

    public function getPermissions(): array
    {
        return $this->permissions;
    }

    public function setPermissions(array $permissions): self
    {
        $this->permissions = $permissions;
        return $this;
    }

    public function addPermission(string $permission): self
    {
        if (!in_array($permission, $this->permissions, true)) {
            $this->permissions[] = $permission;
        }
        return $this;
    }

    public function removePermission(string $permission): self
    {
        $this->permissions = array_filter(
            $this->permissions,
            fn($p) => $p !== $permission
        );
        return $this;
    }

    public function hasPermission(string $permission): bool
    {
        // Check for wildcard permission
        if (in_array('*', $this->permissions, true)) {
            return true;
        }
        
        // Check exact match
        if (in_array($permission, $this->permissions, true)) {
            return true;
        }
        
        // Check for partial wildcard (e.g., "users.*" matches "users.create")
        foreach ($this->permissions as $p) {
            if (str_ends_with($p, '.*')) {
                $prefix = substr($p, 0, -1);
                if (str_starts_with($permission, $prefix)) {
                    return true;
                }
            }
        }
        
        return false;
    }

    public function getCreatedAt(): \DateTimeImmutable
    {
        return $this->createdAt;
    }

    public function toArray(): array
    {
        return [
            'id' => $this->id,
            'name' => $this->name,
            'slug' => $this->slug,
            'description' => $this->description,
            'permissions' => $this->permissions,
            'createdAt' => $this->createdAt->format('c'),
        ];
    }

    /**
     * Create default roles.
     */
    public static function createDefaultRoles(): array
    {
        $admin = new self('Administrator', 'admin');
        $admin->setDescription('Full system access');
        $admin->setPermissions(['*']);

        $manager = new self('Manager', 'manager');
        $manager->setDescription('Can manage users and view reports');
        $manager->setPermissions(['users.*', 'reports.view']);

        $user = new self('User', 'user');
        $user->setDescription('Basic user access');
        $user->setPermissions(['profile.view', 'profile.edit']);

        return [$admin, $manager, $user];
    }
}
