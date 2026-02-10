"""
Validation Utilities - Input validation helpers.

Referenced by:
- user_service.py (email, password validation)
- auth_service.py (credential validation)
"""
import re
from typing import Tuple, Optional


class ValidationError(Exception):
    """Raised when validation fails."""
    def __init__(self, message: str, field: Optional[str] = None):
        super().__init__(message)
        self.field = field
        self.message = message


# Email validation pattern
EMAIL_PATTERN = re.compile(
    r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
)

# Password requirements
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128
PASSWORD_REQUIRES_UPPERCASE = True
PASSWORD_REQUIRES_LOWERCASE = True
PASSWORD_REQUIRES_DIGIT = True
PASSWORD_REQUIRES_SPECIAL = True
SPECIAL_CHARACTERS = "!@#$%^&*()_+-=[]{}|;:,.<>?"


def validate_email(email: str) -> bool:
    """
    Validate email address format.
    
    Args:
        email: Email address to validate
        
    Returns:
        True if email is valid
    """
    if not email:
        return False
    
    if len(email) > 254:  # RFC 5321 limit
        return False
    
    return bool(EMAIL_PATTERN.match(email))


def validate_password(password: str) -> Tuple[bool, str]:
    """
    Validate password against security requirements.
    
    Args:
        password: Password to validate
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not password:
        return False, "Password is required"
    
    if len(password) < MIN_PASSWORD_LENGTH:
        return False, f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
    
    if len(password) > MAX_PASSWORD_LENGTH:
        return False, f"Password must be at most {MAX_PASSWORD_LENGTH} characters"
    
    if PASSWORD_REQUIRES_UPPERCASE and not any(c.isupper() for c in password):
        return False, "Password must contain at least one uppercase letter"
    
    if PASSWORD_REQUIRES_LOWERCASE and not any(c.islower() for c in password):
        return False, "Password must contain at least one lowercase letter"
    
    if PASSWORD_REQUIRES_DIGIT and not any(c.isdigit() for c in password):
        return False, "Password must contain at least one digit"
    
    if PASSWORD_REQUIRES_SPECIAL and not any(c in SPECIAL_CHARACTERS for c in password):
        return False, f"Password must contain at least one special character ({SPECIAL_CHARACTERS})"
    
    return True, ""


def validate_username(username: str) -> Tuple[bool, str]:
    """
    Validate username format.
    
    Args:
        username: Username to validate
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not username:
        return False, "Username is required"
    
    if len(username) < 3:
        return False, "Username must be at least 3 characters"
    
    if len(username) > 50:
        return False, "Username must be at most 50 characters"
    
    # Allow letters, numbers, underscores, hyphens
    if not re.match(r'^[a-zA-Z0-9_-]+$', username):
        return False, "Username can only contain letters, numbers, underscores, and hyphens"
    
    # Must start with a letter
    if not username[0].isalpha():
        return False, "Username must start with a letter"
    
    return True, ""


def validate_phone(phone: str) -> Tuple[bool, str]:
    """
    Validate phone number format.
    
    Args:
        phone: Phone number to validate
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not phone:
        return True, ""  # Phone is optional
    
    # Remove common formatting characters
    cleaned = re.sub(r'[\s\-\(\)\+]', '', phone)
    
    if not cleaned.isdigit():
        return False, "Phone number can only contain digits"
    
    if len(cleaned) < 10 or len(cleaned) > 15:
        return False, "Phone number must be 10-15 digits"
    
    return True, ""


def validate_postal_code(postal_code: str, country: str = "US") -> Tuple[bool, str]:
    """
    Validate postal/zip code for a country.
    
    Args:
        postal_code: Postal code to validate
        country: Country code (default: US)
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not postal_code:
        return False, "Postal code is required"
    
    patterns = {
        "US": r'^\d{5}(-\d{4})?$',  # 12345 or 12345-6789
        "CA": r'^[A-Za-z]\d[A-Za-z][ -]?\d[A-Za-z]\d$',  # A1A 1A1
        "UK": r'^[A-Za-z]{1,2}\d{1,2}[A-Za-z]?\s?\d[A-Za-z]{2}$',  # SW1A 1AA
        "DE": r'^\d{5}$',  # 12345
    }
    
    pattern = patterns.get(country.upper())
    
    if not pattern:
        # Default: allow alphanumeric, 3-10 characters
        if not re.match(r'^[A-Za-z0-9\s\-]{3,10}$', postal_code):
            return False, "Invalid postal code format"
        return True, ""
    
    if not re.match(pattern, postal_code):
        return False, f"Invalid postal code format for {country}"
    
    return True, ""


def sanitize_string(text: str, max_length: int = 1000) -> str:
    """
    Sanitize string input.
    
    Args:
        text: Text to sanitize
        max_length: Maximum allowed length
        
    Returns:
        Sanitized string
    """
    if not text:
        return ""
    
    # Trim whitespace
    sanitized = text.strip()
    
    # Truncate if too long
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length]
    
    # Remove null bytes
    sanitized = sanitized.replace('\x00', '')
    
    return sanitized


def validate_required_fields(data: dict, required: list) -> Tuple[bool, list]:
    """
    Validate that all required fields are present and non-empty.
    
    Args:
        data: Dictionary to validate
        required: List of required field names
        
    Returns:
        Tuple of (all_present, missing_fields)
    """
    missing = []
    
    for field in required:
        value = data.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(field)
    
    return len(missing) == 0, missing
