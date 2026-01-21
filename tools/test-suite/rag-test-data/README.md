# RAG Delta Index Test Data

This directory contains PHP sample files for testing RAG incremental/delta indexing.

## Branch Structure

### 1. `master/` - Main Branch (Base)
The base repository with core classes:
- `User.php` - User entity
- `UserRepository.php` - User data access
- `UserService.php` - User business logic
- `AuthController.php` - Authentication endpoints
- `config.php` - Configuration

### 2. `release-1.0/` - Release Branch
Based on master with:
- Performance optimizations in UserRepository (caching)
- Added rate limiting to AuthController
- Version bump in config

### 3. `feature-1.0/` - Feature: User Roles
Based on master with:
- New `Role.php` entity
- New `RoleService.php`
- Updated User.php with roles support
- Updated UserService with role management

### 4. `feature-1.1/` - Feature: Notifications
Based on master with:
- New `Notification.php` entity
- New `NotificationService.php`
- New `NotificationController.php`
- Updated User.php with notification preferences

## Testing Workflow

1. Create a test repository
2. Push `master/` files to `main` branch
3. Create and push `release-1.0/` files to `release/1.0` branch
4. Create and push `feature-1.0/` files to `feature/user-roles` branch
5. Create and push `feature-1.1/` files to `feature/notifications` branch

## Expected Delta Indexes

| Branch | Base | Delta Files |
|--------|------|-------------|
| release/1.0 | main | UserRepository.php, AuthController.php, config.php |
| feature/user-roles | main | Role.php, RoleService.php, User.php, UserService.php |
| feature/notifications | main | Notification.php, NotificationService.php, NotificationController.php, User.php |
