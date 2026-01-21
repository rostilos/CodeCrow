<?php

declare(strict_types=1);

/**
 * Application configuration.
 */
return [
    'app' => [
        'name' => 'CodeCrow Test App',
        'version' => '1.0.0',
        'environment' => getenv('APP_ENV') ?: 'development',
        'debug' => (bool) (getenv('APP_DEBUG') ?: true),
    ],

    'database' => [
        'driver' => 'mysql',
        'host' => getenv('DB_HOST') ?: 'localhost',
        'port' => (int) (getenv('DB_PORT') ?: 3306),
        'database' => getenv('DB_DATABASE') ?: 'codecrow_test',
        'username' => getenv('DB_USERNAME') ?: 'root',
        'password' => getenv('DB_PASSWORD') ?: '',
        'charset' => 'utf8mb4',
        'collation' => 'utf8mb4_unicode_ci',
    ],

    'auth' => [
        'jwt_secret' => getenv('JWT_SECRET') ?: 'your-secret-key-change-in-production',
        'jwt_ttl' => 3600, // 1 hour
        'refresh_ttl' => 86400, // 24 hours
    ],

    'mail' => [
        'driver' => 'smtp',
        'host' => getenv('MAIL_HOST') ?: 'localhost',
        'port' => (int) (getenv('MAIL_PORT') ?: 587),
        'username' => getenv('MAIL_USERNAME') ?: '',
        'password' => getenv('MAIL_PASSWORD') ?: '',
        'from_address' => getenv('MAIL_FROM') ?: 'noreply@example.com',
        'from_name' => 'CodeCrow Test',
    ],

    'logging' => [
        'level' => getenv('LOG_LEVEL') ?: 'debug',
        'path' => __DIR__ . '/../logs/app.log',
    ],
];
