<?php

declare(strict_types=1);

namespace App\Controller;

use App\Service\UserService;

/**
 * Controller for authentication endpoints.
 */
class AuthController
{
    private UserService $userService;
    private array $config;

    public function __construct(UserService $userService, array $config)
    {
        $this->userService = $userService;
        $this->config = $config;
    }

    public function login(array $request): array
    {
        $email = $request['email'] ?? null;
        $password = $request['password'] ?? null;

        if (!$email || !$password) {
            return $this->errorResponse('Email and password are required', 400);
        }

        $user = $this->userService->getUserByEmail($email);
        
        if ($user === null || !$user->verifyPassword($password)) {
            return $this->errorResponse('Invalid credentials', 401);
        }

        if (!$user->isActive()) {
            return $this->errorResponse('Account is deactivated', 403);
        }

        $user->recordLogin();
        
        $token = $this->generateToken($user->getId());

        return $this->successResponse([
            'token' => $token,
            'user' => $user->toArray(),
            'expiresIn' => $this->config['jwt_ttl'] ?? 3600,
        ]);
    }

    public function register(array $request): array
    {
        $email = $request['email'] ?? null;
        $password = $request['password'] ?? null;
        $firstName = $request['firstName'] ?? null;
        $lastName = $request['lastName'] ?? null;

        if (!$email || !$password || !$firstName || !$lastName) {
            return $this->errorResponse('All fields are required', 400);
        }

        try {
            $user = $this->userService->createUser($email, $password, $firstName, $lastName);
            $token = $this->generateToken($user->getId());

            return $this->successResponse([
                'token' => $token,
                'user' => $user->toArray(),
            ], 201);
        } catch (\InvalidArgumentException $e) {
            return $this->errorResponse($e->getMessage(), 400);
        }
    }

    public function logout(array $request): array
    {
        // In a real implementation, we would invalidate the token
        return $this->successResponse(['message' => 'Logged out successfully']);
    }

    public function refreshToken(array $request): array
    {
        $token = $request['token'] ?? null;
        
        if (!$token) {
            return $this->errorResponse('Token is required', 400);
        }

        $payload = $this->verifyToken($token);
        
        if ($payload === null) {
            return $this->errorResponse('Invalid or expired token', 401);
        }

        $user = $this->userService->getUserById($payload['userId']);
        
        if ($user === null || !$user->isActive()) {
            return $this->errorResponse('User not found or inactive', 401);
        }

        $newToken = $this->generateToken($user->getId());

        return $this->successResponse([
            'token' => $newToken,
            'expiresIn' => $this->config['jwt_ttl'] ?? 3600,
        ]);
    }

    public function changePassword(array $request): array
    {
        $userId = $request['userId'] ?? null;
        $currentPassword = $request['currentPassword'] ?? null;
        $newPassword = $request['newPassword'] ?? null;

        if (!$userId || !$currentPassword || !$newPassword) {
            return $this->errorResponse('All fields are required', 400);
        }

        $user = $this->userService->getUserById($userId);
        
        if ($user === null) {
            return $this->errorResponse('User not found', 404);
        }

        if (!$user->verifyPassword($currentPassword)) {
            return $this->errorResponse('Current password is incorrect', 400);
        }

        try {
            $this->userService->updateUser($userId, ['password' => $newPassword]);
            return $this->successResponse(['message' => 'Password changed successfully']);
        } catch (\InvalidArgumentException $e) {
            return $this->errorResponse($e->getMessage(), 400);
        }
    }

    private function generateToken(int $userId): string
    {
        $secret = $this->config['jwt_secret'] ?? 'default-secret';
        $ttl = $this->config['jwt_ttl'] ?? 3600;

        $header = base64_encode(json_encode(['alg' => 'HS256', 'typ' => 'JWT']));
        $payload = base64_encode(json_encode([
            'userId' => $userId,
            'iat' => time(),
            'exp' => time() + $ttl,
        ]));
        $signature = hash_hmac('sha256', "$header.$payload", $secret);

        return "$header.$payload.$signature";
    }

    private function verifyToken(string $token): ?array
    {
        $parts = explode('.', $token);
        if (count($parts) !== 3) {
            return null;
        }

        [$header, $payload, $signature] = $parts;
        $secret = $this->config['jwt_secret'] ?? 'default-secret';
        
        $expectedSignature = hash_hmac('sha256', "$header.$payload", $secret);
        
        if (!hash_equals($expectedSignature, $signature)) {
            return null;
        }

        $data = json_decode(base64_decode($payload), true);
        
        if ($data['exp'] < time()) {
            return null;
        }

        return $data;
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
