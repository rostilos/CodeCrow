package org.rostilos.codecrow.webserver.controller.auth;

import jakarta.validation.Valid;
import org.rostilos.codecrow.core.dto.message.MessageResponse;
import org.rostilos.codecrow.core.model.user.twofactor.TwoFactorAuth;
import org.rostilos.codecrow.webserver.dto.request.auth.TwoFactorSetupRequest;
import org.rostilos.codecrow.webserver.dto.request.auth.TwoFactorVerifyRequest;
import org.rostilos.codecrow.webserver.dto.response.auth.TwoFactorEnableResponse;
import org.rostilos.codecrow.webserver.dto.response.auth.TwoFactorSetupResponse;
import org.rostilos.codecrow.webserver.dto.response.auth.TwoFactorStatusResponse;
import org.rostilos.codecrow.webserver.service.auth.TwoFactorAuthService;
import org.rostilos.codecrow.security.service.UserDetailsImpl;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;

import java.util.Arrays;
import java.util.Optional;

@RestController
@RequestMapping("/api/auth/2fa")
public class TwoFactorAuthController {

    private final TwoFactorAuthService twoFactorAuthService;

    public TwoFactorAuthController(TwoFactorAuthService twoFactorAuthService) {
        this.twoFactorAuthService = twoFactorAuthService;
    }

    /**
     * Get current 2FA status
     */
    @GetMapping("/status")
    public ResponseEntity<TwoFactorStatusResponse> getStatus(
            @AuthenticationPrincipal UserDetailsImpl userDetails) {
        
        Optional<TwoFactorAuth> twoFactorAuth = twoFactorAuthService.getTwoFactorStatus(userDetails.getId());
        
        if (twoFactorAuth.isEmpty() || !twoFactorAuth.get().isEnabled()) {
            return ResponseEntity.ok(new TwoFactorStatusResponse(false, null, 0));
        }
        
        TwoFactorAuth auth = twoFactorAuth.get();
        int remainingBackupCodes = 0;
        if (auth.getBackupCodes() != null) {
            remainingBackupCodes = (int) Arrays.stream(auth.getBackupCodes().split(","))
                    .filter(code -> !code.equals("USED"))
                    .count();
        }
        
        return ResponseEntity.ok(new TwoFactorStatusResponse(
                true,
                auth.getTwoFactorType().name(),
                remainingBackupCodes
        ));
    }

    /**
     * Initialize 2FA setup (TOTP or Email)
     */
    @PostMapping("/setup")
    public ResponseEntity<?> initializeSetup(
            @AuthenticationPrincipal UserDetailsImpl userDetails,
            @Valid @RequestBody TwoFactorSetupRequest request) {
        
        try {
            TwoFactorSetupResponse response;
            
            if ("TOTP".equalsIgnoreCase(request.getType())) {
                response = twoFactorAuthService.initializeTotpSetup(userDetails.getId());
            } else if ("EMAIL".equalsIgnoreCase(request.getType())) {
                response = twoFactorAuthService.initializeEmailSetup(userDetails.getId());
            } else {
                return ResponseEntity.badRequest()
                        .body(new MessageResponse("Invalid 2FA type. Use 'TOTP' or 'EMAIL'"));
            }
            
            return ResponseEntity.ok(response);
        } catch (RuntimeException e) {
            return ResponseEntity.badRequest()
                    .body(new MessageResponse(e.getMessage()));
        }
    }

    /**
     * Verify code and enable 2FA
     */
    @PostMapping("/verify")
    public ResponseEntity<?> verifyAndEnable(
            @AuthenticationPrincipal UserDetailsImpl userDetails,
            @Valid @RequestBody TwoFactorVerifyRequest request) {
        
        try {
            String[] backupCodes = twoFactorAuthService.verifyAndEnable(
                    userDetails.getId(), 
                    request.getCode()
            );
            
            return ResponseEntity.ok(new TwoFactorEnableResponse(
                    backupCodes,
                    true,
                    "Two-factor authentication enabled successfully"
            ));
        } catch (RuntimeException e) {
            return ResponseEntity.badRequest()
                    .body(new MessageResponse(e.getMessage()));
        }
    }

    /**
     * Disable 2FA
     */
    @PostMapping("/disable")
    public ResponseEntity<?> disable(
            @AuthenticationPrincipal UserDetailsImpl userDetails,
            @Valid @RequestBody TwoFactorVerifyRequest request) {
        
        try {
            twoFactorAuthService.disable(userDetails.getId(), request.getCode());
            return ResponseEntity.ok(new MessageResponse("Two-factor authentication disabled successfully"));
        } catch (RuntimeException e) {
            return ResponseEntity.badRequest()
                    .body(new MessageResponse(e.getMessage()));
        }
    }

    /**
     * Regenerate backup codes
     */
    @PostMapping("/backup-codes/regenerate")
    public ResponseEntity<?> regenerateBackupCodes(
            @AuthenticationPrincipal UserDetailsImpl userDetails,
            @Valid @RequestBody TwoFactorVerifyRequest request) {
        
        try {
            String[] backupCodes = twoFactorAuthService.regenerateBackupCodes(
                    userDetails.getId(), 
                    request.getCode()
            );
            
            return ResponseEntity.ok(new TwoFactorEnableResponse(
                    backupCodes,
                    true,
                    "Backup codes regenerated successfully"
            ));
        } catch (RuntimeException e) {
            return ResponseEntity.badRequest()
                    .body(new MessageResponse(e.getMessage()));
        }
    }

    /**
     * Resend email verification code (for Email 2FA)
     */
    @PostMapping("/resend-code")
    public ResponseEntity<?> resendEmailCode(
            @AuthenticationPrincipal UserDetailsImpl userDetails) {
        
        try {
            twoFactorAuthService.sendLoginEmailCode(userDetails.getId());
            return ResponseEntity.ok(new MessageResponse("Verification code sent to your email"));
        } catch (RuntimeException e) {
            return ResponseEntity.badRequest()
                    .body(new MessageResponse(e.getMessage()));
        }
    }
}
