"""
User Service - Business logic for user management.

Dependencies:
- models.user (User, UserRepository, UserRole, UserStatus)
- utils.validators (validate_email, validate_password)

Referenced by:
- auth_service.py (authentication flows)
- order_service.py (user validation for orders)
- user_controller.ts (via API)
"""
import logging
from typing import Optional, List
from datetime import datetime

from ..models.user import User, UserRepository, UserRole, UserStatus, UserPreferences
from ..utils.validators import validate_email, validate_password, ValidationError

logger = logging.getLogger(__name__)


class UserServiceError(Exception):
    """Base exception for user service errors."""
    pass


class UserNotFoundError(UserServiceError):
    """Raised when user is not found."""
    pass


class DuplicateEmailError(UserServiceError):
    """Raised when email already exists."""
    pass


class InvalidUserDataError(UserServiceError):
    """Raised when user data is invalid."""
    pass


class UserService:
    """
    Service layer for user management operations.
    
    Provides business logic for:
    - User registration and account creation
    - User profile updates
    - Account status management
    - Role assignments
    - User search and listing
    """
    
    def __init__(self, repository: Optional[UserRepository] = None):
        """
        Initialize UserService.
        
        Args:
            repository: UserRepository instance. Creates new one if not provided.
        """
        self.repository = repository or UserRepository()
        logger.info("UserService initialized")
    
    def create_user(
        self,
        email: str,
        username: str,
        password: str,
        role: UserRole = UserRole.USER
    ) -> User:
        """
        Create a new user account.
        
        Args:
            email: User's email address
            username: Display name
            password: Plain text password (will be hashed)
            role: Initial user role
            
        Returns:
            Created User instance
            
        Raises:
            InvalidUserDataError: If email or password validation fails
            DuplicateEmailError: If email already exists
        """
        # Validate email
        if not validate_email(email):
            raise InvalidUserDataError(f"Invalid email format: {email}")
        
        # Validate password
        is_valid, message = validate_password(password)
        if not is_valid:
            raise InvalidUserDataError(f"Invalid password: {message}")
        
        # Check for existing email
        existing = self.repository.find_by_email(email)
        if existing:
            raise DuplicateEmailError(f"Email already registered: {email}")
        
        # Hash password (in production, use bcrypt/argon2)
        password_hash = self._hash_password(password)
        
        # Create user
        user = User(
            id=0,  # Will be assigned by repository
            email=email,
            username=username,
            password_hash=password_hash,
            role=role,
            status=UserStatus.PENDING,
            preferences=UserPreferences()
        )
        
        created_user = self.repository.create(user)
        logger.info(f"Created user: {created_user.id} ({email})")
        
        return created_user
    
    def get_user(self, user_id: int) -> User:
        """
        Get user by ID.
        
        Args:
            user_id: User's unique identifier
            
        Returns:
            User instance
            
        Raises:
            UserNotFoundError: If user doesn't exist
        """
        user = self.repository.find_by_id(user_id)
        if not user:
            raise UserNotFoundError(f"User not found: {user_id}")
        return user
    
    def get_user_by_email(self, email: str) -> User:
        """
        Get user by email address.
        
        Args:
            email: User's email address
            
        Returns:
            User instance
            
        Raises:
            UserNotFoundError: If user doesn't exist
        """
        user = self.repository.find_by_email(email)
        if not user:
            raise UserNotFoundError(f"User not found: {email}")
        return user
    
    def update_user(
        self,
        user_id: int,
        username: Optional[str] = None,
        email: Optional[str] = None,
        preferences: Optional[dict] = None
    ) -> User:
        """
        Update user profile.
        
        Args:
            user_id: User's unique identifier
            username: New username (optional)
            email: New email (optional)
            preferences: Updated preferences dict (optional)
            
        Returns:
            Updated User instance
            
        Raises:
            UserNotFoundError: If user doesn't exist
            InvalidUserDataError: If new data is invalid
            DuplicateEmailError: If new email already exists
        """
        user = self.get_user(user_id)
        
        if username:
            if len(username) < 3:
                raise InvalidUserDataError("Username must be at least 3 characters")
            user.username = username
        
        if email and email != user.email:
            if not validate_email(email):
                raise InvalidUserDataError(f"Invalid email format: {email}")
            
            existing = self.repository.find_by_email(email)
            if existing:
                raise DuplicateEmailError(f"Email already registered: {email}")
            
            user.email = email
        
        if preferences:
            user.preferences.email_notifications = preferences.get(
                "email_notifications", user.preferences.email_notifications
            )
            user.preferences.push_notifications = preferences.get(
                "push_notifications", user.preferences.push_notifications
            )
            user.preferences.theme = preferences.get("theme", user.preferences.theme)
            user.preferences.language = preferences.get("language", user.preferences.language)
        
        updated_user = self.repository.update(user)
        logger.info(f"Updated user: {user_id}")
        
        return updated_user
    
    def change_password(self, user_id: int, old_password: str, new_password: str) -> bool:
        """
        Change user's password.
        
        Args:
            user_id: User's unique identifier
            old_password: Current password for verification
            new_password: New password to set
            
        Returns:
            True if password was changed
            
        Raises:
            UserNotFoundError: If user doesn't exist
            InvalidUserDataError: If old password is wrong or new password is invalid
        """
        user = self.get_user(user_id)
        
        # Verify old password
        if not self._verify_password(old_password, user.password_hash):
            raise InvalidUserDataError("Current password is incorrect")
        
        # Validate new password
        is_valid, message = validate_password(new_password)
        if not is_valid:
            raise InvalidUserDataError(f"Invalid new password: {message}")
        
        # Update password
        user.password_hash = self._hash_password(new_password)
        self.repository.update(user)
        
        logger.info(f"Password changed for user: {user_id}")
        return True
    
    def activate_user(self, user_id: int) -> User:
        """
        Activate a user account.
        
        Args:
            user_id: User's unique identifier
            
        Returns:
            Updated User instance
        """
        user = self.get_user(user_id)
        user.activate()
        return self.repository.update(user)
    
    def suspend_user(self, user_id: int, reason: str = None) -> User:
        """
        Suspend a user account.
        
        Args:
            user_id: User's unique identifier
            reason: Reason for suspension
            
        Returns:
            Updated User instance
        """
        user = self.get_user(user_id)
        user.suspend(reason)
        logger.warning(f"User suspended: {user_id}, reason: {reason}")
        return self.repository.update(user)
    
    def assign_role(self, user_id: int, role: UserRole, assigned_by: int) -> User:
        """
        Assign a role to user.
        
        Args:
            user_id: Target user's identifier
            role: Role to assign
            assigned_by: User ID of the admin making the assignment
            
        Returns:
            Updated User instance
        """
        user = self.get_user(user_id)
        admin = self.get_user(assigned_by)
        
        # Only admins can assign roles
        if not admin.has_permission(UserRole.ADMIN):
            raise UserServiceError("Permission denied: Only admins can assign roles")
        
        # Can't assign higher role than own
        if not admin.has_permission(role):
            raise UserServiceError("Permission denied: Cannot assign role higher than own")
        
        user.role = role
        logger.info(f"Role {role.value} assigned to user {user_id} by {assigned_by}")
        
        return self.repository.update(user)
    
    def list_users(
        self,
        status: Optional[UserStatus] = None,
        role: Optional[UserRole] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[User]:
        """
        List users with optional filtering.
        
        Args:
            status: Filter by account status
            role: Filter by role
            limit: Maximum results to return
            offset: Pagination offset
            
        Returns:
            List of User instances
        """
        users = self.repository.find_all(status=status)
        
        if role:
            users = [u for u in users if u.role == role]
        
        return users[offset:offset + limit]
    
    def delete_user(self, user_id: int, deleted_by: int) -> bool:
        """
        Soft delete a user account.
        
        Args:
            user_id: User to delete
            deleted_by: Admin performing the deletion
            
        Returns:
            True if deleted successfully
        """
        admin = self.get_user(deleted_by)
        
        if not admin.has_permission(UserRole.ADMIN):
            raise UserServiceError("Permission denied: Only admins can delete users")
        
        result = self.repository.delete(user_id)
        if result:
            logger.info(f"User {user_id} deleted by {deleted_by}")
        
        return result
    
    def _hash_password(self, password: str) -> str:
        """Hash password. In production, use bcrypt/argon2."""
        import hashlib
        return hashlib.sha256(password.encode()).hexdigest()
    
    def _verify_password(self, password: str, password_hash: str) -> bool:
        """Verify password against hash."""
        return self._hash_password(password) == password_hash
