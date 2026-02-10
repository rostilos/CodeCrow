package com.codecrow.api;

import org.springframework.http.ResponseEntity;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;
import org.springframework.beans.factory.annotation.Autowired;
import javax.validation.Valid;
import java.util.Map;
import java.util.HashMap;
import java.util.Optional;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Authentication Controller - REST API endpoints for authentication.
 * 
 * Java implementation demonstrating cross-language retrieval.
 * This controller integrates with Python AuthService via internal API.
 * 
 * Dependencies (via internal API):
 * - AuthService (Python backend)
 * - UserService (Python backend)
 */
@RestController
@RequestMapping("/api/auth")
public class AuthController {
    
    private static final Logger logger = LoggerFactory.getLogger(AuthController.class);
    
    // In production, inject via dependency injection
    private final String backendUrl = System.getenv().getOrDefault("BACKEND_URL", "http://localhost:8000");
    
    /**
     * POST /api/auth/login - Authenticate user
     * 
     * Request body:
     * - email: User's email address
     * - password: User's password
     * 
     * Response:
     * - user: User object
     * - token: Session token
     */
    @PostMapping("/login")
    public ResponseEntity<Map<String, Object>> login(
            @Valid @RequestBody LoginRequest request,
            @RequestHeader(value = "X-Forwarded-For", required = false) String ipAddress,
            @RequestHeader(value = "User-Agent", required = false) String userAgent
    ) {
        logger.info("Login attempt for: {}", request.getEmail());
        
        try {
            // Validate input
            if (request.getEmail() == null || request.getEmail().isEmpty()) {
                return errorResponse(HttpStatus.BAD_REQUEST, "Email is required");
            }
            if (request.getPassword() == null || request.getPassword().isEmpty()) {
                return errorResponse(HttpStatus.BAD_REQUEST, "Password is required");
            }
            
            // Call Python AuthService
            Map<String, Object> authRequest = new HashMap<>();
            authRequest.put("email", request.getEmail());
            authRequest.put("password", request.getPassword());
            authRequest.put("ip_address", ipAddress);
            authRequest.put("user_agent", userAgent);
            
            // In production, use RestTemplate or WebClient
            // AuthResponse response = restTemplate.postForObject(
            //     backendUrl + "/api/auth/login",
            //     authRequest,
            //     AuthResponse.class
            // );
            
            // Mock response for demo
            Map<String, Object> response = new HashMap<>();
            response.put("success", true);
            response.put("user", mockUser(request.getEmail()));
            response.put("token", generateMockToken());
            response.put("message", "Login successful");
            
            logger.info("Login successful for: {}", request.getEmail());
            return ResponseEntity.ok(response);
            
        } catch (Exception e) {
            logger.error("Login failed for {}: {}", request.getEmail(), e.getMessage());
            return errorResponse(HttpStatus.UNAUTHORIZED, "Invalid credentials");
        }
    }
    
    /**
     * POST /api/auth/logout - Terminate session
     * 
     * Headers:
     * - Authorization: Bearer {token}
     */
    @PostMapping("/logout")
    public ResponseEntity<Map<String, Object>> logout(
            @RequestHeader(value = "Authorization", required = false) String authHeader
    ) {
        logger.info("Logout request received");
        
        try {
            String token = extractToken(authHeader);
            
            if (token == null) {
                return errorResponse(HttpStatus.BAD_REQUEST, "Authorization token required");
            }
            
            // Call Python AuthService to invalidate session
            // In production: restTemplate.postForObject(backendUrl + "/api/auth/logout", ...)
            
            Map<String, Object> response = new HashMap<>();
            response.put("success", true);
            response.put("message", "Logout successful");
            
            logger.info("Logout successful");
            return ResponseEntity.ok(response);
            
        } catch (Exception e) {
            logger.error("Logout failed: {}", e.getMessage());
            return errorResponse(HttpStatus.INTERNAL_SERVER_ERROR, "Logout failed");
        }
    }
    
    /**
     * POST /api/auth/refresh - Refresh session token
     * 
     * Headers:
     * - Authorization: Bearer {token}
     * 
     * Response:
     * - token: New session token
     */
    @PostMapping("/refresh")
    public ResponseEntity<Map<String, Object>> refreshToken(
            @RequestHeader(value = "Authorization", required = false) String authHeader
    ) {
        logger.info("Token refresh request received");
        
        try {
            String token = extractToken(authHeader);
            
            if (token == null) {
                return errorResponse(HttpStatus.BAD_REQUEST, "Authorization token required");
            }
            
            // Validate and refresh token via Python AuthService
            // In production: Use restTemplate/WebClient
            
            Map<String, Object> response = new HashMap<>();
            response.put("success", true);
            response.put("token", generateMockToken());
            response.put("message", "Token refreshed");
            
            logger.info("Token refresh successful");
            return ResponseEntity.ok(response);
            
        } catch (Exception e) {
            logger.error("Token refresh failed: {}", e.getMessage());
            return errorResponse(HttpStatus.UNAUTHORIZED, "Session expired");
        }
    }
    
    /**
     * POST /api/auth/password-reset/request - Request password reset
     * 
     * Request body:
     * - email: User's email address
     */
    @PostMapping("/password-reset/request")
    public ResponseEntity<Map<String, Object>> requestPasswordReset(
            @Valid @RequestBody PasswordResetRequest request
    ) {
        logger.info("Password reset requested for: {}", request.getEmail());
        
        try {
            if (request.getEmail() == null || request.getEmail().isEmpty()) {
                return errorResponse(HttpStatus.BAD_REQUEST, "Email is required");
            }
            
            // Call Python AuthService
            // Note: Always return success to prevent email enumeration
            
            Map<String, Object> response = new HashMap<>();
            response.put("success", true);
            response.put("message", "If the email exists, a reset link has been sent");
            
            return ResponseEntity.ok(response);
            
        } catch (Exception e) {
            logger.error("Password reset request failed: {}", e.getMessage());
            // Still return success to prevent enumeration
            Map<String, Object> response = new HashMap<>();
            response.put("success", true);
            response.put("message", "If the email exists, a reset link has been sent");
            return ResponseEntity.ok(response);
        }
    }
    
    /**
     * POST /api/auth/password-reset/confirm - Confirm password reset
     * 
     * Request body:
     * - token: Reset token from email
     * - new_password: New password
     */
    @PostMapping("/password-reset/confirm")
    public ResponseEntity<Map<String, Object>> confirmPasswordReset(
            @Valid @RequestBody PasswordResetConfirmRequest request
    ) {
        logger.info("Password reset confirmation received");
        
        try {
            if (request.getToken() == null || request.getToken().isEmpty()) {
                return errorResponse(HttpStatus.BAD_REQUEST, "Reset token is required");
            }
            if (request.getNewPassword() == null || request.getNewPassword().length() < 8) {
                return errorResponse(HttpStatus.BAD_REQUEST, "Password must be at least 8 characters");
            }
            
            // Call Python AuthService to reset password
            // Validates token and updates password
            
            Map<String, Object> response = new HashMap<>();
            response.put("success", true);
            response.put("message", "Password has been reset successfully");
            
            logger.info("Password reset successful");
            return ResponseEntity.ok(response);
            
        } catch (Exception e) {
            logger.error("Password reset failed: {}", e.getMessage());
            return errorResponse(HttpStatus.BAD_REQUEST, "Invalid or expired reset token");
        }
    }
    
    /**
     * GET /api/auth/session - Get current session info
     * 
     * Headers:
     * - Authorization: Bearer {token}
     */
    @GetMapping("/session")
    public ResponseEntity<Map<String, Object>> getSession(
            @RequestHeader(value = "Authorization", required = false) String authHeader
    ) {
        try {
            String token = extractToken(authHeader);
            
            if (token == null) {
                return errorResponse(HttpStatus.UNAUTHORIZED, "Authorization required");
            }
            
            // Validate session via Python AuthService
            
            Map<String, Object> response = new HashMap<>();
            response.put("success", true);
            response.put("user", mockUser("user@example.com"));
            response.put("expires_in", 86400); // seconds
            
            return ResponseEntity.ok(response);
            
        } catch (Exception e) {
            logger.error("Session validation failed: {}", e.getMessage());
            return errorResponse(HttpStatus.UNAUTHORIZED, "Invalid session");
        }
    }
    
    /**
     * GET /api/auth/sessions - List active sessions for current user
     * 
     * Headers:
     * - Authorization: Bearer {token}
     */
    @GetMapping("/sessions")
    public ResponseEntity<Map<String, Object>> listSessions(
            @RequestHeader(value = "Authorization", required = false) String authHeader
    ) {
        try {
            String token = extractToken(authHeader);
            
            if (token == null) {
                return errorResponse(HttpStatus.UNAUTHORIZED, "Authorization required");
            }
            
            // Get active sessions from Python AuthService
            
            Map<String, Object> response = new HashMap<>();
            response.put("success", true);
            response.put("sessions", new Object[]{}); // List of active sessions
            response.put("count", 1);
            
            return ResponseEntity.ok(response);
            
        } catch (Exception e) {
            logger.error("Failed to list sessions: {}", e.getMessage());
            return errorResponse(HttpStatus.INTERNAL_SERVER_ERROR, "Failed to retrieve sessions");
        }
    }
    
    // ==================== Helper Methods ====================
    
    private String extractToken(String authHeader) {
        if (authHeader != null && authHeader.startsWith("Bearer ")) {
            return authHeader.substring(7);
        }
        return null;
    }
    
    private ResponseEntity<Map<String, Object>> errorResponse(HttpStatus status, String message) {
        Map<String, Object> response = new HashMap<>();
        response.put("success", false);
        response.put("error", message);
        return ResponseEntity.status(status).body(response);
    }
    
    private Map<String, Object> mockUser(String email) {
        Map<String, Object> user = new HashMap<>();
        user.put("id", 1);
        user.put("email", email);
        user.put("username", email.split("@")[0]);
        user.put("role", "user");
        user.put("status", "active");
        return user;
    }
    
    private String generateMockToken() {
        return "mock-session-token-" + System.currentTimeMillis();
    }
}

// Request/Response DTOs
class LoginRequest {
    private String email;
    private String password;
    
    public String getEmail() { return email; }
    public void setEmail(String email) { this.email = email; }
    public String getPassword() { return password; }
    public void setPassword(String password) { this.password = password; }
}

class PasswordResetRequest {
    private String email;
    
    public String getEmail() { return email; }
    public void setEmail(String email) { this.email = email; }
}

class PasswordResetConfirmRequest {
    private String token;
    private String newPassword;
    
    public String getToken() { return token; }
    public void setToken(String token) { this.token = token; }
    public String getNewPassword() { return newPassword; }
    public void setNewPassword(String newPassword) { this.newPassword = newPassword; }
}
