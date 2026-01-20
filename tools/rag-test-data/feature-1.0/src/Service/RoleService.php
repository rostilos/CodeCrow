<?php

declare(strict_types=1);

namespace App\Service;

use App\Entity\Role;
use App\Repository\RoleRepository;

/**
 * Service for role business logic.
 * 
 * Feature: User Roles
 */
class RoleService
{
    private RoleRepository $roleRepository;

    public function __construct(RoleRepository $roleRepository)
    {
        $this->roleRepository = $roleRepository;
    }

    /**
     * Create a new role.
     */
    public function createRole(string $name, string $slug, ?string $description = null, array $permissions = []): Role
    {
        // Validate slug uniqueness
        if ($this->roleRepository->findBySlug($slug) !== null) {
            throw new \InvalidArgumentException('Role with this slug already exists');
        }

        // Validate slug format
        if (!preg_match('/^[a-z0-9_-]+$/', $slug)) {
            throw new \InvalidArgumentException('Slug must contain only lowercase letters, numbers, hyphens, and underscores');
        }

        // Validate permissions format
        $this->validatePermissions($permissions);

        $role = new Role($name, $slug);
        $role->setDescription($description);
        $role->setPermissions($permissions);

        return $this->roleRepository->save($role);
    }

    /**
     * Update an existing role.
     */
    public function updateRole(int $roleId, array $data): Role
    {
        $role = $this->roleRepository->findById($roleId);
        if ($role === null) {
            throw new \InvalidArgumentException('Role not found');
        }

        // Prevent modifying system roles
        if (in_array($role->getSlug(), ['admin', 'user'], true)) {
            if (isset($data['slug']) && $data['slug'] !== $role->getSlug()) {
                throw new \InvalidArgumentException('Cannot change slug of system roles');
            }
        }

        if (isset($data['name'])) {
            $role->setName($data['name']);
        }

        if (isset($data['slug'])) {
            $existing = $this->roleRepository->findBySlug($data['slug']);
            if ($existing !== null && $existing->getId() !== $roleId) {
                throw new \InvalidArgumentException('Role with this slug already exists');
            }
            $role->setSlug($data['slug']);
        }

        if (isset($data['description'])) {
            $role->setDescription($data['description']);
        }

        if (isset($data['permissions'])) {
            $this->validatePermissions($data['permissions']);
            $role->setPermissions($data['permissions']);
        }

        return $this->roleRepository->save($role);
    }

    /**
     * Delete a role.
     */
    public function deleteRole(int $roleId): bool
    {
        $role = $this->roleRepository->findById($roleId);
        if ($role === null) {
            throw new \InvalidArgumentException('Role not found');
        }

        // Prevent deleting system roles
        if (in_array($role->getSlug(), ['admin', 'user'], true)) {
            throw new \InvalidArgumentException('Cannot delete system roles');
        }

        return $this->roleRepository->delete($roleId);
    }

    /**
     * Get a role by ID.
     */
    public function getRoleById(int $roleId): ?Role
    {
        return $this->roleRepository->findById($roleId);
    }

    /**
     * Get a role by slug.
     */
    public function getRoleBySlug(string $slug): ?Role
    {
        return $this->roleRepository->findBySlug($slug);
    }

    /**
     * List all roles.
     */
    public function listRoles(): array
    {
        $roles = $this->roleRepository->findAll();
        return array_map(fn(Role $r) => $r->toArray(), $roles);
    }

    /**
     * Add a permission to a role.
     */
    public function addPermissionToRole(int $roleId, string $permission): Role
    {
        $role = $this->roleRepository->findById($roleId);
        if ($role === null) {
            throw new \InvalidArgumentException('Role not found');
        }

        $this->validatePermissions([$permission]);
        $role->addPermission($permission);

        return $this->roleRepository->save($role);
    }

    /**
     * Remove a permission from a role.
     */
    public function removePermissionFromRole(int $roleId, string $permission): Role
    {
        $role = $this->roleRepository->findById($roleId);
        if ($role === null) {
            throw new \InvalidArgumentException('Role not found');
        }

        $role->removePermission($permission);

        return $this->roleRepository->save($role);
    }

    /**
     * Get all available permissions.
     */
    public function getAvailablePermissions(): array
    {
        return [
            'users' => [
                'users.view' => 'View users',
                'users.create' => 'Create users',
                'users.edit' => 'Edit users',
                'users.delete' => 'Delete users',
                'users.*' => 'All user permissions',
            ],
            'roles' => [
                'roles.view' => 'View roles',
                'roles.create' => 'Create roles',
                'roles.edit' => 'Edit roles',
                'roles.delete' => 'Delete roles',
                'roles.*' => 'All role permissions',
            ],
            'profile' => [
                'profile.view' => 'View own profile',
                'profile.edit' => 'Edit own profile',
            ],
            'reports' => [
                'reports.view' => 'View reports',
                'reports.export' => 'Export reports',
            ],
            'admin' => [
                '*' => 'Full system access (Admin)',
            ],
        ];
    }

    /**
     * Initialize default roles.
     */
    public function initializeDefaultRoles(): array
    {
        $defaultRoles = Role::createDefaultRoles();
        $created = [];

        foreach ($defaultRoles as $role) {
            if ($this->roleRepository->findBySlug($role->getSlug()) === null) {
                $created[] = $this->roleRepository->save($role);
            }
        }

        return $created;
    }

    /**
     * Validate permission formats.
     */
    private function validatePermissions(array $permissions): void
    {
        foreach ($permissions as $permission) {
            if (!is_string($permission)) {
                throw new \InvalidArgumentException('Permissions must be strings');
            }

            // Allow wildcard
            if ($permission === '*') {
                continue;
            }

            // Validate format: resource.action or resource.*
            if (!preg_match('/^[a-z_]+(\.[a-z_*]+)?$/', $permission)) {
                throw new \InvalidArgumentException("Invalid permission format: {$permission}");
            }
        }
    }
}
