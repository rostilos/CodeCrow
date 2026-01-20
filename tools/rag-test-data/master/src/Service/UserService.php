<?php

declare(strict_types=1);

namespace App\Service;

use App\Entity\User;
use App\Repository\UserRepository;

/**
 * Service for user business logic.
 */
class UserService
{
    private UserRepository $userRepository;

    public function __construct(UserRepository $userRepository)
    {
        $this->userRepository = $userRepository;
    }

    public function createUser(string $email, string $password, string $firstName, string $lastName): User
    {
        // Check if email already exists
        if ($this->userRepository->findByEmail($email) !== null) {
            throw new \InvalidArgumentException('Email already registered');
        }

        // Validate email format
        if (!filter_var($email, FILTER_VALIDATE_EMAIL)) {
            throw new \InvalidArgumentException('Invalid email format');
        }

        // Validate password strength
        $this->validatePassword($password);

        $user = new User($email, $firstName, $lastName);
        $user->setPassword($password);

        return $this->userRepository->save($user);
    }

    public function updateUser(int $userId, array $data): User
    {
        $user = $this->userRepository->findById($userId);
        if ($user === null) {
            throw new \InvalidArgumentException('User not found');
        }

        if (isset($data['email'])) {
            $existing = $this->userRepository->findByEmail($data['email']);
            if ($existing !== null && $existing->getId() !== $userId) {
                throw new \InvalidArgumentException('Email already in use');
            }
            $user->setEmail($data['email']);
        }

        if (isset($data['firstName'])) {
            $user->setFirstName($data['firstName']);
        }

        if (isset($data['lastName'])) {
            $user->setLastName($data['lastName']);
        }

        if (isset($data['password'])) {
            $this->validatePassword($data['password']);
            $user->setPassword($data['password']);
        }

        return $this->userRepository->save($user);
    }

    public function deactivateUser(int $userId): User
    {
        $user = $this->userRepository->findById($userId);
        if ($user === null) {
            throw new \InvalidArgumentException('User not found');
        }

        $user->setActive(false);
        return $this->userRepository->save($user);
    }

    public function activateUser(int $userId): User
    {
        $user = $this->userRepository->findById($userId);
        if ($user === null) {
            throw new \InvalidArgumentException('User not found');
        }

        $user->setActive(true);
        return $this->userRepository->save($user);
    }

    public function getUserById(int $userId): ?User
    {
        return $this->userRepository->findById($userId);
    }

    public function getUserByEmail(string $email): ?User
    {
        return $this->userRepository->findByEmail($email);
    }

    public function listUsers(int $page = 1, int $perPage = 20): array
    {
        $offset = ($page - 1) * $perPage;
        $users = $this->userRepository->findAll($perPage, $offset);
        $total = $this->userRepository->countAll();

        return [
            'users' => array_map(fn(User $u) => $u->toArray(), $users),
            'pagination' => [
                'page' => $page,
                'perPage' => $perPage,
                'total' => $total,
                'totalPages' => (int) ceil($total / $perPage),
            ],
        ];
    }

    public function deleteUser(int $userId): bool
    {
        $user = $this->userRepository->findById($userId);
        if ($user === null) {
            throw new \InvalidArgumentException('User not found');
        }

        return $this->userRepository->delete($userId);
    }

    private function validatePassword(string $password): void
    {
        if (strlen($password) < 8) {
            throw new \InvalidArgumentException('Password must be at least 8 characters');
        }

        if (!preg_match('/[A-Z]/', $password)) {
            throw new \InvalidArgumentException('Password must contain at least one uppercase letter');
        }

        if (!preg_match('/[a-z]/', $password)) {
            throw new \InvalidArgumentException('Password must contain at least one lowercase letter');
        }

        if (!preg_match('/[0-9]/', $password)) {
            throw new \InvalidArgumentException('Password must contain at least one number');
        }
    }
}
