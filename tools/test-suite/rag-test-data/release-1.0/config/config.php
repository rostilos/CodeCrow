<?php

declare(strict_types=1);

/**
 * Application configuration.
 * 
 * Release 1.0 - Production ready configuration.
 */
return [
    'app' => [
        'name' => 'CodeCrow Test App',
        'version' => '1.0.0-release',
        'environment' => getenv('APP_ENV') ?: 'production',
        'debug' => (bool) (getenv('APP_DEBUG') ?: false),
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
        'pool' => [
            'min' => 2,
            'max' => 10,
        ],
    ],

    'auth' => [
        'jwt_secret' => getenv('JWT_SECRET') ?: 'your-secret-key-change-in-production',
        'jwt_ttl' => 3600, // 1 hour
        'refresh_ttl' => 86400, // 24 hours
    ],

    'cache' => [
        'enabled' => true,
        'driver' => 'memory', // Use 'redis' in production
        'ttl' => 300, // 5 minutes
        'prefix' => 'codecrow_',
    ],

    'rate_limiting' => [
        'enabled' => true,
        'login' => [
            'max_attempts' => 5,
            'window' => 900, // 15 minutes
        ],
        'registration' => [
            'max_attempts' => 3,
            'window' => 900,
        ],
        'api' => [
            'requests_per_minute' => 60,
        ],
    ],

    'mail' => [
        'driver' => 'smtp',
        'host' => getenv('MAIL_HOST') ?: 'localhost',
        'port' => (int) (getenv('MAIL_PORT') ?: 587),
        'username' => getenv('MAIL_USERNAME') ?: '',
        'password' => getenv('MAIL_PASSWORD') ?: '',
        'from_address' => getenv('MAIL_FROM') ?: 'noreply@example.com',
        'from_name' => 'CodeCrow Test',
        'encryption' => 'tls',
    ],

    'logging' => [
        'level' => getenv('LOG_LEVEL') ?: 'info',
        'path' => __DIR__ . '/../logs/app.log',
        'max_files' => 30,
    ],

    'security' => [
        'cors' => [
            'allowed_origins' => explode(',', getenv('CORS_ORIGINS') ?: '*'),
            'allowed_methods' => ['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'],
            'allowed_headers' => ['Content-Type', 'Authorization'],
        ],
    ],
];
