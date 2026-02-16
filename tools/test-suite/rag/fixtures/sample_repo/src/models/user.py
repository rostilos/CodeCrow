"""
User Model - Core entity for the application.

This model is referenced by:
- user_service.py (CRUD operations)
- auth_service.py (authentication)
- order_service.py (order ownership)
- order.py (foreign key relationship)
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List
from enum import Enum


class UserRole(Enum):
    """User roles for access control."""
    GUEST = "guest"
    USER = "user"
    ADMIN = "admin"
    SUPERADMIN = "superadmin"


class UserStatus(Enum):
    """User account status."""
    PENDING = "pending"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"


@dataclass
class UserPreferences:
    """User notification and display preferences."""
    email_notifications: bool = True
    push_notifications: bool = False
    theme: str = "light"
    language: str = "en"
    timezone: str = "UTC"


@dataclass
class User:
    """
    User entity representing an application user.
    
    Attributes:
        id: Unique identifier
        email: User's email address (unique)
        username: Display name
        password_hash: Hashed password (never store plain text!)
        role: User's access level
        status: Account status
        preferences: User preferences
        created_at: Account creation timestamp
        updated_at: Last modification timestamp
        last_login: Last successful login timestamp
    """
    id: int
    email: str
    username: str
    password_hash: str
    role: UserRole = UserRole.USER
    status: UserStatus = UserStatus.PENDING
    preferences: UserPreferences = field(default_factory=UserPreferences)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None
    last_login: Optional[datetime] = None
    
    def __post_init__(self):
        """Validate user data after initialization."""
        if not self.email or "@" not in self.email:
            raise ValueError("Invalid email address")
        if not self.username or len(self.username) < 3:
            raise ValueError("Username must be at least 3 characters")
    
    def is_active(self) -> bool:
        """Check if user account is active."""
        return self.status == UserStatus.ACTIVE
    
    def has_permission(self, required_role: UserRole) -> bool:
        """Check if user has required role or higher."""
        role_hierarchy = {
            UserRole.GUEST: 0,
            UserRole.USER: 1,
            UserRole.ADMIN: 2,
            UserRole.SUPERADMIN: 3
        }
        return role_hierarchy.get(self.role, 0) >= role_hierarchy.get(required_role, 0)
    
    def update_last_login(self) -> None:
        """Update last login timestamp."""
        self.last_login = datetime.utcnow()
        self.updated_at = datetime.utcnow()
    
    def activate(self) -> None:
        """Activate user account."""
        if self.status == UserStatus.DELETED:
            raise ValueError("Cannot activate deleted account")
        self.status = UserStatus.ACTIVE
        self.updated_at = datetime.utcnow()
    
    def suspend(self, reason: str = None) -> None:
        """Suspend user account."""
        self.status = UserStatus.SUSPENDED
        self.updated_at = datetime.utcnow()
    
    def to_dict(self, include_sensitive: bool = False) -> dict:
        """Convert user to dictionary representation."""
        data = {
            "id": self.id,
            "email": self.email,
            "username": self.username,
            "role": self.role.value,
            "status": self.status.value,
            "preferences": {
                "email_notifications": self.preferences.email_notifications,
                "push_notifications": self.preferences.push_notifications,
                "theme": self.preferences.theme,
                "language": self.preferences.language,
            },
            "created_at": self.created_at.isoformat(),
            "last_login": self.last_login.isoformat() if self.last_login else None,
        }
        if include_sensitive:
            data["password_hash"] = self.password_hash
        return data


class UserRepository:
    """
    Repository pattern for User persistence.
    
    In production, this would use a real database.
    This mock implementation uses in-memory storage.
    """
    
    def __init__(self):
        self._users: dict[int, User] = {}
        self._email_index: dict[str, int] = {}
        self._next_id = 1
    
    def create(self, user: User) -> User:
        """Create a new user."""
        if user.email in self._email_index:
            raise ValueError(f"Email {user.email} already exists")
        
        user.id = self._next_id
        self._next_id += 1
        
        self._users[user.id] = user
        self._email_index[user.email] = user.id
        
        return user
    
    def find_by_id(self, user_id: int) -> Optional[User]:
        """Find user by ID."""
        return self._users.get(user_id)
    
    def find_by_email(self, email: str) -> Optional[User]:
        """Find user by email address."""
        user_id = self._email_index.get(email)
        if user_id:
            return self._users.get(user_id)
        return None
    
    def find_all(self, status: Optional[UserStatus] = None) -> List[User]:
        """Find all users, optionally filtered by status."""
        users = list(self._users.values())
        if status:
            users = [u for u in users if u.status == status]
        return users
    
    def update(self, user: User) -> User:
        """Update existing user."""
        if user.id not in self._users:
            raise ValueError(f"User {user.id} not found")
        
        user.updated_at = datetime.utcnow()
        self._users[user.id] = user
        
        return user
    
    def delete(self, user_id: int) -> bool:
        """Soft delete user by ID."""
        user = self._users.get(user_id)
        if not user:
            return False
        
        user.status = UserStatus.DELETED
        user.updated_at = datetime.utcnow()
        
        return True
