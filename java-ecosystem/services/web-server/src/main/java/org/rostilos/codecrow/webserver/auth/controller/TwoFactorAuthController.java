package org.rostilos.codecrow.webserver.auth.controller;

import jakarta.validation.Valid;
import org.rostilos.codecrow.webserver.generic.dto.message.MessageResponse;
import org.rostilos.codecrow.core.model.user.twofactor.TwoFactorAuth;
import org.rostilos.codecrow.webserver.auth.dto.request.TwoFactorSetupRequest;
import org.rostilos.codecrow.webserver.auth.dto.request.TwoFactorVerifyRequest;
import org.rostilos.codecrow.webserver.auth.dto.response.TwoFactorEnableResponse;
import org.rostilos.codecrow.webserver.auth.dto.response.TwoFactorSetupResponse;
import org.rostilos.codecrow.webserver.auth.dto.response.TwoFactorStatusResponse;
import org.rostilos.codecrow.webserver.auth.service.TwoFactorAuthService;
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
    public ResponseEntity<TwoFactorSetupResponse> initializeSetup(
            @AuthenticationPrincipal UserDetailsImpl userDetails,
            @Valid @RequestBody TwoFactorSetupRequest request
    ) {
        TwoFactorSetupResponse response = switch (request.getType()) {
            case "TOTP" -> twoFactorAuthService.initializeTotpSetup(userDetails.getId());
            case "EMAIL" -> twoFactorAuthService.initializeEmailSetup(userDetails.getId());
            default -> throw new IllegalStateException("Unexpected value: " + request.getType());
        };

        return ResponseEntity.ok(response);
    }

    /**
     * Verify code and enable 2FA
     */
    @PostMapping("/verify")
    public ResponseEntity<TwoFactorEnableResponse> verifyAndEnable(
            @AuthenticationPrincipal UserDetailsImpl userDetails,
            @Valid @RequestBody TwoFactorVerifyRequest request
    ) {
        String[] backupCodes = twoFactorAuthService.verifyAndEnable(
                userDetails.getId(),
                request.getCode()
        );

        return ResponseEntity.ok(new TwoFactorEnableResponse(
                backupCodes,
                true,
                "Two-factor authentication enabled successfully"
        ));
    }

    /**
     * Disable 2FA
     */
    @PostMapping("/disable")
    public ResponseEntity<MessageResponse> disable(
            @AuthenticationPrincipal UserDetailsImpl userDetails,
            @Valid @RequestBody TwoFactorVerifyRequest request
    ) {
        twoFactorAuthService.disable(userDetails.getId(), request.getCode());
        return ResponseEntity.ok(new MessageResponse("Two-factor authentication disabled successfully"));
    }

    /**
     * Regenerate backup codes
     */
    @PostMapping("/backup-codes/regenerate")
    public ResponseEntity<TwoFactorEnableResponse> regenerateBackupCodes(
            @AuthenticationPrincipal UserDetailsImpl userDetails,
            @Valid @RequestBody TwoFactorVerifyRequest request) {
        
        String[] backupCodes = twoFactorAuthService.regenerateBackupCodes(
                userDetails.getId(),
                request.getCode()
        );

        return ResponseEntity.ok(new TwoFactorEnableResponse(
                backupCodes,
                true,
                "Backup codes regenerated successfully"
        ));
    }

    /**
     * Resend email verification code (for Email 2FA)
     */
    @PostMapping("/resend-code")
    public ResponseEntity<MessageResponse> resendEmailCode(
            @AuthenticationPrincipal UserDetailsImpl userDetails
    ) {
        twoFactorAuthService.sendLoginEmailCode(userDetails.getId());
        return ResponseEntity.ok(new MessageResponse("Verification code sent to your email"));
    }
}
