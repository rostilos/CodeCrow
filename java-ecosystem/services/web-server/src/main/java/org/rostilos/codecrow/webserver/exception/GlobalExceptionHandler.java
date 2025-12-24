package org.rostilos.codecrow.webserver.exception;

import jakarta.persistence.EntityExistsException;
import org.rostilos.codecrow.webserver.dto.message.ErrorMessageResponse;
import org.rostilos.codecrow.webserver.exception.comment.*;
import org.rostilos.codecrow.webserver.exception.user.UserIdNotFoundException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.authentication.BadCredentialsException;
import org.springframework.validation.FieldError;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

import javax.security.auth.RefreshFailedException;
import java.security.GeneralSecurityException;
import java.util.HashMap;
import java.util.Map;
import java.util.NoSuchElementException;

@RestControllerAdvice
public class GlobalExceptionHandler {
    
    private static final Logger log = LoggerFactory.getLogger(GlobalExceptionHandler.class);
    
    @ExceptionHandler(SecurityException.class)
    public ResponseEntity<ErrorMessageResponse> handleSecurityException(SecurityException ex) {
        return ResponseEntity
                .status(HttpStatus.FORBIDDEN)
                .body(new ErrorMessageResponse(ex.getMessage(), HttpStatus.FORBIDDEN));
    }

    @ExceptionHandler(TwoFactorRequiredException.class)
    public ResponseEntity<ErrorMessageResponse> handleTwoFactorRequired(TwoFactorRequiredException ex) {
        return ResponseEntity
                .status(HttpStatus.FORBIDDEN)
                .body(new ErrorMessageResponse(ex.getMessage(), HttpStatus.FORBIDDEN));
    }

    @ExceptionHandler(TwoFactorInvalidException.class)
    public ResponseEntity<ErrorMessageResponse> handleTwoFactorInvalid(TwoFactorInvalidException ex) {
        return ResponseEntity
                .status(HttpStatus.FORBIDDEN)
                .body(new ErrorMessageResponse(ex.getMessage(), HttpStatus.FORBIDDEN));
    }

    @ExceptionHandler(InvalidResetTokenException.class)
    public ResponseEntity<ErrorMessageResponse> handleInvalidResetToken(InvalidResetTokenException ex) {
        return ResponseEntity
                .status(HttpStatus.FORBIDDEN)
                .body(new ErrorMessageResponse(ex.getMessage(), HttpStatus.FORBIDDEN));
    }

    @ExceptionHandler(IllegalArgumentException.class)
    public ResponseEntity<ErrorMessageResponse> handleIllegalArgument(IllegalArgumentException ex) {
        return ResponseEntity
                .badRequest()
                .body(new ErrorMessageResponse(ex.getMessage(), HttpStatus.BAD_REQUEST));
    }

    @ExceptionHandler(NoSuchElementException.class)
    public ResponseEntity<ErrorMessageResponse> handleNoSuchEntityException(NoSuchElementException ex) { // Fixed param
        return ResponseEntity
                .status(HttpStatus.NOT_FOUND)
                .body(new ErrorMessageResponse(ex.getMessage(), HttpStatus.NOT_FOUND));
    }

    @ExceptionHandler(RuntimeException.class)
    public ResponseEntity<ErrorMessageResponse> handleRuntimeException(RuntimeException ex) {
        log.error("Runtime exception: {}", ex.getMessage(), ex);
        return ResponseEntity
                .status(HttpStatus.INTERNAL_SERVER_ERROR)
                .body(new ErrorMessageResponse("An unexpected error occurred. Please try again later.", HttpStatus.INTERNAL_SERVER_ERROR));
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<ErrorMessageResponse> handleGeneric(Exception ex) {
        log.error("Unhandled exception: {}", ex.getMessage(), ex);
        return ResponseEntity
                .status(HttpStatus.INTERNAL_SERVER_ERROR)
                .body(new ErrorMessageResponse("An unexpected error occurred. Please try again later.", HttpStatus.INTERNAL_SERVER_ERROR));
    }

    @ExceptionHandler(InvalidProjectRequestException.class)
    public ResponseEntity<ErrorMessageResponse> handleInvalidProjectRequest(InvalidProjectRequestException ex) {
        return ResponseEntity
                .badRequest()
                .body(new ErrorMessageResponse(ex.getMessage(), HttpStatus.BAD_REQUEST));
    }

    @ExceptionHandler(BadCredentialsException.class)
    public ResponseEntity<ErrorMessageResponse> handleBadCredentials(BadCredentialsException ex) { // Fixed param
        return ResponseEntity
                .status(HttpStatus.UNAUTHORIZED)
                .body(new ErrorMessageResponse(ex.getMessage(), HttpStatus.UNAUTHORIZED));
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<Map<String, String>> handleValidationExceptions(
            MethodArgumentNotValidException ex) {
        Map<String, String> errors = new HashMap<>();
        ex.getBindingResult().getAllErrors().forEach(error -> {
            String fieldName = ((FieldError) error).getField();
            String errorMessage = error.getDefaultMessage();
            errors.put(fieldName, errorMessage);
        });
        return new ResponseEntity<>(errors, HttpStatus.BAD_REQUEST);
    }
    
    // =====================================================
    // Comment Command Exception Handlers
    // =====================================================
    
    @ExceptionHandler(RateLimitExceededException.class)
    public ResponseEntity<Map<String, Object>> handleRateLimitExceeded(RateLimitExceededException ex) {
        log.warn("Rate limit exceeded: type={}, retryAfter={}s", ex.getLimitType(), ex.getRetryAfterSeconds());
        
        Map<String, Object> response = new HashMap<>();
        response.put("error", ex.getMessage());
        response.put("errorCode", ex.getErrorCode());
        response.put("limitType", ex.getLimitType());
        response.put("retryAfterSeconds", ex.getRetryAfterSeconds());
        response.put("retryable", ex.isRetryable());
        
        return ResponseEntity
                .status(HttpStatus.TOO_MANY_REQUESTS)
                .header("Retry-After", String.valueOf(ex.getRetryAfterSeconds()))
                .body(response);
    }
    
    @ExceptionHandler(UnknownCommandException.class)
    public ResponseEntity<Map<String, Object>> handleUnknownCommand(UnknownCommandException ex) {
        log.debug("Unknown command received: {}", ex.getCommandName());
        
        Map<String, Object> response = new HashMap<>();
        response.put("error", ex.getMessage());
        response.put("errorCode", ex.getErrorCode());
        response.put("command", ex.getCommandName());
        
        return ResponseEntity
                .status(HttpStatus.BAD_REQUEST)
                .body(response);
    }
    
    @ExceptionHandler(CommandDisabledException.class)
    public ResponseEntity<Map<String, Object>> handleCommandDisabled(CommandDisabledException ex) {
        log.debug("Command disabled: command={}, projectId={}", ex.getCommandName(), ex.getProjectId());
        
        Map<String, Object> response = new HashMap<>();
        response.put("error", ex.getMessage());
        response.put("errorCode", ex.getErrorCode());
        if (ex.getCommandName() != null) {
            response.put("command", ex.getCommandName());
        }
        
        return ResponseEntity
                .status(HttpStatus.FORBIDDEN)
                .body(response);
    }
    
    @ExceptionHandler(CommandUnauthorizedException.class)
    public ResponseEntity<Map<String, Object>> handleCommandUnauthorized(CommandUnauthorizedException ex) {
        log.warn("Unauthorized command attempt: user={}, command={}", ex.getVcsUsername(), ex.getCommandName());
        
        Map<String, Object> response = new HashMap<>();
        response.put("error", ex.getMessage());
        response.put("errorCode", ex.getErrorCode());
        if (ex.getRequiredRole() != null) {
            response.put("requiredRole", ex.getRequiredRole());
        }
        
        return ResponseEntity
                .status(HttpStatus.FORBIDDEN)
                .body(response);
    }
    
    @ExceptionHandler(CommandExecutionException.class)
    public ResponseEntity<Map<String, Object>> handleCommandExecution(CommandExecutionException ex) {
        log.error("Command execution failed: command={}, phase={}, message={}", 
                ex.getCommandName(), ex.getExecutionPhase(), ex.getMessage(), ex);
        
        Map<String, Object> response = new HashMap<>();
        response.put("error", ex.getMessage());
        response.put("errorCode", ex.getErrorCode());
        response.put("command", ex.getCommandName());
        response.put("phase", ex.getExecutionPhase());
        response.put("retryable", ex.isRetryable());
        
        return ResponseEntity
                .status(HttpStatus.INTERNAL_SERVER_ERROR)
                .body(response);
    }
    
    @ExceptionHandler(WebhookSignatureException.class)
    public ResponseEntity<Map<String, Object>> handleWebhookSignature(WebhookSignatureException ex) {
        log.warn("Webhook signature validation failed: provider={}", ex.getProvider());
        
        Map<String, Object> response = new HashMap<>();
        response.put("error", "Webhook signature validation failed");
        response.put("errorCode", ex.getErrorCode());
        
        return ResponseEntity
                .status(HttpStatus.UNAUTHORIZED)
                .body(response);
    }
    
    @ExceptionHandler(WebhookParseException.class)
    public ResponseEntity<Map<String, Object>> handleWebhookParse(WebhookParseException ex) {
        log.warn("Webhook parse failed: provider={}, event={}, message={}", 
                ex.getProvider(), ex.getEventType(), ex.getMessage());
        
        Map<String, Object> response = new HashMap<>();
        response.put("error", ex.getMessage());
        response.put("errorCode", ex.getErrorCode());
        
        return ResponseEntity
                .status(HttpStatus.BAD_REQUEST)
                .body(response);
    }
    
    @ExceptionHandler(CommentCommandException.class)
    public ResponseEntity<Map<String, Object>> handleCommentCommand(CommentCommandException ex) {
        log.error("Comment command error: errorCode={}, message={}", ex.getErrorCode(), ex.getMessage(), ex);
        
        Map<String, Object> response = new HashMap<>();
        response.put("error", ex.getMessage());
        response.put("errorCode", ex.getErrorCode());
        response.put("retryable", ex.isRetryable());
        
        return ResponseEntity
                .status(HttpStatus.INTERNAL_SERVER_ERROR)
                .body(response);
    }

    @ExceptionHandler(RefreshFailedException.class)
    public ResponseEntity<ErrorMessageResponse> handleInvalidRefreshToken(RefreshFailedException ex) {
        return ResponseEntity
                .status(HttpStatus.FORBIDDEN)
                .body(new ErrorMessageResponse(ex.getMessage(), HttpStatus.FORBIDDEN));
    }

    @ExceptionHandler(EntityExistsException.class)
    public ResponseEntity<ErrorMessageResponse> handleEntityExistsException(EntityExistsException ex) {
        return ResponseEntity
                .status(HttpStatus.CONFLICT)
                .body(new ErrorMessageResponse(ex.getMessage(), HttpStatus.CONFLICT));
    }

    @ExceptionHandler(UserIdNotFoundException.class)
    public ResponseEntity<ErrorMessageResponse> handleUserIdNotFoundException(UserIdNotFoundException ex) {
        return ResponseEntity
                .status(HttpStatus.NOT_FOUND)
                .body(new ErrorMessageResponse(ex.getMessage(), HttpStatus.NOT_FOUND));
    }

    @ExceptionHandler(GeneralSecurityException.class)
    public ResponseEntity<ErrorMessageResponse> handleGeneralSecurityException(GeneralSecurityException ex) {
        log.error("General security exception: {}", ex.getMessage(), ex);
        return ResponseEntity
                .status(HttpStatus.INTERNAL_SERVER_ERROR)
                .body(new ErrorMessageResponse("A security error occurred. Please try again later.", HttpStatus.INTERNAL_SERVER_ERROR));
    }
}
