"""
Authentication Service - Handles user authentication and session management.

Dependencies:
- services.user_service (UserService)
- models.user (User, UserStatus)
- utils.validators (validate_email)

Referenced by:
- auth_controller.java (API endpoints)
"""
import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional, Tuple
from dataclasses import dataclass

from .user_service import UserService, UserNotFoundError, InvalidUserDataError
from ..models.user import User, UserStatus
from ..utils.validators import validate_email

logger = logging.getLogger(__name__)


class AuthenticationError(Exception):
    """Base exception for authentication errors."""
    pass


class InvalidCredentialsError(AuthenticationError):
    """Raised when credentials are invalid."""
    pass


class AccountDisabledError(AuthenticationError):
    """Raised when account is not active."""
    pass


class SessionExpiredError(AuthenticationError):
    """Raised when session has expired."""
    pass


class TokenInvalidError(AuthenticationError):
    """Raised when token is invalid or expired."""
    pass


@dataclass
class Session:
    """User session data."""
    token: str
    user_id: int
    created_at: datetime
    expires_at: datetime
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    
    def is_expired(self) -> bool:
        """Check if session has expired."""
        return datetime.utcnow() > self.expires_at


@dataclass
class PasswordResetToken:
    """Password reset token data."""
    token: str
    user_id: int
    created_at: datetime
    expires_at: datetime
    used: bool = False


class AuthService:
    """
    Service layer for authentication operations.
    
    Provides:
    - User login/logout
    - Session management
    - Password reset flows
    - Token validation
    - Security logging
    """
    
    # Session configuration
    SESSION_DURATION_HOURS = 24
    RESET_TOKEN_DURATION_HOURS = 1
    MAX_LOGIN_ATTEMPTS = 5
    LOCKOUT_DURATION_MINUTES = 15
    
    def __init__(self, user_service: Optional[UserService] = None):
        """
        Initialize AuthService.
        
        Args:
            user_service: UserService instance. Creates new one if not provided.
        """
        self.user_service = user_service or UserService()
        
        # In-memory storage (use Redis/DB in production)
        self._sessions: dict[str, Session] = {}
        self._reset_tokens: dict[str, PasswordResetToken] = {}
        self._login_attempts: dict[str, list[datetime]] = {}
        
        logger.info("AuthService initialized")
    
    def login(
        self,
        email: str,
        password: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> Tuple[User, str]:
        """
        Authenticate user and create session.
        
        Args:
            email: User's email address
            password: User's password
            ip_address: Client IP for logging
            user_agent: Client user agent for logging
            
        Returns:
            Tuple of (User, session_token)
            
        Raises:
            InvalidCredentialsError: If credentials are wrong
            AccountDisabledError: If account is not active
            AuthenticationError: If account is locked
        """
        # Check rate limiting
        if self._is_account_locked(email):
            logger.warning(f"Login blocked - account locked: {email}")
            raise AuthenticationError("Account temporarily locked due to too many failed attempts")
        
        # Validate email format
        if not validate_email(email):
            self._record_failed_attempt(email)
            raise InvalidCredentialsError("Invalid credentials")
        
        try:
            # Get user
            user = self.user_service.get_user_by_email(email)
            
            # Verify password
            if not self.user_service._verify_password(password, user.password_hash):
                self._record_failed_attempt(email)
                logger.warning(f"Login failed - invalid password: {email}")
                raise InvalidCredentialsError("Invalid credentials")
            
            # Check account status
            if not user.is_active():
                logger.warning(f"Login failed - account not active: {email} ({user.status.value})")
                raise AccountDisabledError(f"Account is {user.status.value}")
            
            # Clear failed attempts on successful login
            self._clear_failed_attempts(email)
            
            # Update last login
            user.update_last_login()
            self.user_service.repository.update(user)
            
            # Create session
            session_token = self._create_session(user.id, ip_address, user_agent)
            
            logger.info(f"Login successful: {email} from {ip_address}")
            return user, session_token
            
        except UserNotFoundError:
            self._record_failed_attempt(email)
            logger.warning(f"Login failed - user not found: {email}")
            raise InvalidCredentialsError("Invalid credentials")
    
    def logout(self, session_token: str) -> bool:
        """
        Terminate user session.
        
        Args:
            session_token: Session token to invalidate
            
        Returns:
            True if session was terminated
        """
        if session_token in self._sessions:
            session = self._sessions.pop(session_token)
            logger.info(f"Logout successful: user_id={session.user_id}")
            return True
        return False
    
    def validate_session(self, session_token: str) -> User:
        """
        Validate session and return user.
        
        Args:
            session_token: Session token to validate
            
        Returns:
            User instance if session is valid
            
        Raises:
            SessionExpiredError: If session has expired
            TokenInvalidError: If token is invalid
        """
        session = self._sessions.get(session_token)
        
        if not session:
            raise TokenInvalidError("Invalid session token")
        
        if session.is_expired():
            self._sessions.pop(session_token, None)
            raise SessionExpiredError("Session has expired")
        
        return self.user_service.get_user(session.user_id)
    
    def refresh_session(self, session_token: str) -> str:
        """
        Refresh session and return new token.
        
        Args:
            session_token: Current session token
            
        Returns:
            New session token
        """
        user = self.validate_session(session_token)
        
        # Invalidate old session
        old_session = self._sessions.pop(session_token)
        
        # Create new session
        new_token = self._create_session(
            user.id,
            old_session.ip_address,
            old_session.user_agent
        )
        
        logger.info(f"Session refreshed: user_id={user.id}")
        return new_token
    
    def request_password_reset(self, email: str) -> Optional[str]:
        """
        Generate password reset token.
        
        Args:
            email: User's email address
            
        Returns:
            Reset token if user exists, None otherwise
        """
        try:
            user = self.user_service.get_user_by_email(email)
            
            # Generate token
            token = secrets.token_urlsafe(32)
            reset_token = PasswordResetToken(
                token=token,
                user_id=user.id,
                created_at=datetime.utcnow(),
                expires_at=datetime.utcnow() + timedelta(hours=self.RESET_TOKEN_DURATION_HOURS)
            )
            
            self._reset_tokens[token] = reset_token
            
            logger.info(f"Password reset requested: {email}")
            return token
            
        except UserNotFoundError:
            logger.warning(f"Password reset requested for unknown email: {email}")
            return None  # Don't reveal if email exists
    
    def reset_password(self, token: str, new_password: str) -> bool:
        """
        Reset password using token.
        
        Args:
            token: Reset token
            new_password: New password to set
            
        Returns:
            True if password was reset
            
        Raises:
            TokenInvalidError: If token is invalid or expired
            InvalidUserDataError: If new password is invalid
        """
        reset_token = self._reset_tokens.get(token)
        
        if not reset_token:
            raise TokenInvalidError("Invalid reset token")
        
        if reset_token.used:
            raise TokenInvalidError("Reset token already used")
        
        if datetime.utcnow() > reset_token.expires_at:
            raise TokenInvalidError("Reset token expired")
        
        # Get user
        user = self.user_service.get_user(reset_token.user_id)
        
        # Validate new password
        from ..utils.validators import validate_password
        is_valid, message = validate_password(new_password)
        if not is_valid:
            raise InvalidUserDataError(f"Invalid password: {message}")
        
        # Update password
        user.password_hash = self.user_service._hash_password(new_password)
        self.user_service.repository.update(user)
        
        # Mark token as used
        reset_token.used = True
        
        # Invalidate all existing sessions for this user
        self._invalidate_user_sessions(user.id)
        
        logger.info(f"Password reset successful: user_id={user.id}")
        return True
    
    def get_active_sessions(self, user_id: int) -> list[Session]:
        """Get all active sessions for a user."""
        return [
            s for s in self._sessions.values()
            if s.user_id == user_id and not s.is_expired()
        ]
    
    def _create_session(
        self,
        user_id: int,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> str:
        """Create new session for user."""
        token = secrets.token_urlsafe(32)
        
        session = Session(
            token=token,
            user_id=user_id,
            created_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(hours=self.SESSION_DURATION_HOURS),
            ip_address=ip_address,
            user_agent=user_agent
        )
        
        self._sessions[token] = session
        return token
    
    def _invalidate_user_sessions(self, user_id: int) -> int:
        """Invalidate all sessions for a user."""
        tokens_to_remove = [
            token for token, session in self._sessions.items()
            if session.user_id == user_id
        ]
        
        for token in tokens_to_remove:
            self._sessions.pop(token, None)
        
        return len(tokens_to_remove)
    
    def _is_account_locked(self, email: str) -> bool:
        """Check if account is locked due to failed attempts."""
        attempts = self._login_attempts.get(email, [])
        
        # Remove old attempts
        cutoff = datetime.utcnow() - timedelta(minutes=self.LOCKOUT_DURATION_MINUTES)
        recent_attempts = [a for a in attempts if a > cutoff]
        self._login_attempts[email] = recent_attempts
        
        return len(recent_attempts) >= self.MAX_LOGIN_ATTEMPTS
    
    def _record_failed_attempt(self, email: str) -> None:
        """Record a failed login attempt."""
        if email not in self._login_attempts:
            self._login_attempts[email] = []
        self._login_attempts[email].append(datetime.utcnow())
    
    def _clear_failed_attempts(self, email: str) -> None:
        """Clear failed login attempts for email."""
        self._login_attempts.pop(email, None)
