package org.rostilos.codecrow.webserver.controller.auth;

import java.util.HashSet;
import java.util.List;
import java.util.Optional;
import java.util.Set;

import jakarta.persistence.EntityExistsException;
import jakarta.validation.Valid;

import org.rostilos.codecrow.core.model.user.RefreshToken;
import org.rostilos.codecrow.core.model.user.ERole;
import org.rostilos.codecrow.core.model.user.Role;
import org.rostilos.codecrow.core.model.user.User;
import org.rostilos.codecrow.core.model.user.account_type.EAccountType;
import org.rostilos.codecrow.core.model.user.status.EStatus;
import org.rostilos.codecrow.core.model.user.twofactor.ETwoFactorType;
import org.rostilos.codecrow.core.model.user.twofactor.TwoFactorAuth;
import org.rostilos.codecrow.webserver.dto.message.ErrorMessageResponse;
import org.rostilos.codecrow.webserver.dto.response.auth.JwtResponse;
import org.rostilos.codecrow.webserver.dto.response.auth.ResetTokenValidationResponse;
import org.rostilos.codecrow.webserver.dto.response.auth.TwoFactorRequiredResponse;
import org.rostilos.codecrow.webserver.dto.message.MessageResponse;
import org.rostilos.codecrow.core.persistence.repository.user.RoleRepository;
import org.rostilos.codecrow.core.persistence.repository.user.UserRepository;
import org.rostilos.codecrow.webserver.dto.request.auth.ForgotPasswordRequest;
import org.rostilos.codecrow.webserver.dto.request.auth.LoginRequest;
import org.rostilos.codecrow.webserver.dto.request.auth.RefreshTokenRequest;
import org.rostilos.codecrow.webserver.dto.request.auth.ResetPasswordRequest;
import org.rostilos.codecrow.webserver.dto.request.auth.SignupRequest;
import org.rostilos.codecrow.webserver.dto.request.auth.TwoFactorLoginRequest;
import org.rostilos.codecrow.webserver.dto.request.auth.ValidateResetTokenRequest;
import org.rostilos.codecrow.webserver.exception.InvalidResetTokenException;
import org.rostilos.codecrow.webserver.exception.TwoFactorInvalidException;
import org.rostilos.codecrow.webserver.service.auth.PasswordResetService;
import org.rostilos.codecrow.webserver.service.auth.RefreshTokenService;
import org.rostilos.codecrow.webserver.service.auth.TwoFactorAuthService;
import org.rostilos.codecrow.security.jwt.utils.JwtUtils;
import org.rostilos.codecrow.security.service.UserDetailsImpl;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.authentication.AuthenticationManager;
import org.springframework.security.authentication.BadCredentialsException;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.web.bind.annotation.CrossOrigin;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import javax.security.auth.RefreshFailedException;

@CrossOrigin(origins = "*", maxAge = 3600)
@RestController
@RequestMapping("/api/auth")
public class AuthController {
    private final AuthenticationManager authenticationManager;
    private final UserRepository userRepository;
    private final PasswordEncoder encoder;
    private final JwtUtils jwtUtils;
    private final RoleRepository roleRepository;
    private final TwoFactorAuthService twoFactorAuthService;
    private final PasswordResetService passwordResetService;
    private final RefreshTokenService refreshTokenService;

    public AuthController(
        AuthenticationManager authenticationManager,
        UserRepository userRepository,
        PasswordEncoder encoder,
        JwtUtils jwtUtils,
        RoleRepository roleRepository,
        TwoFactorAuthService twoFactorAuthService,
        PasswordResetService passwordResetService,
        RefreshTokenService refreshTokenService
    ) {
        this.authenticationManager = authenticationManager;
        this.userRepository = userRepository;
        this.encoder = encoder;
        this.jwtUtils = jwtUtils;
        this.roleRepository = roleRepository;
        this.twoFactorAuthService = twoFactorAuthService;
        this.passwordResetService = passwordResetService;
        this.refreshTokenService = refreshTokenService;
    }

    @PostMapping("/login")
    public ResponseEntity<?> authenticateUser(@Valid @RequestBody LoginRequest loginRequest) {
        try {
            Authentication authentication = authenticationManager.authenticate(
                    new UsernamePasswordAuthenticationToken(loginRequest.getUsername(), loginRequest.getPassword()));

            UserDetailsImpl userDetails = (UserDetailsImpl) authentication.getPrincipal();
            
            // Check if user has 2FA enabled
            Optional<TwoFactorAuth> twoFactorAuth = twoFactorAuthService.getTwoFactorStatus(userDetails.getId());
            
            if (twoFactorAuth.isPresent() && twoFactorAuth.get().isEnabled()) {
                // Generate temporary token for 2FA verification
                String tempToken = jwtUtils.generateTempToken(userDetails.getId(), userDetails.getUsername());
                
                if (twoFactorAuth.get().getTwoFactorType() == ETwoFactorType.EMAIL) {
                    twoFactorAuthService.sendLoginEmailCode(userDetails.getId());
                }
                
                return ResponseEntity.ok(new TwoFactorRequiredResponse(
                        true,
                        tempToken,
                        twoFactorAuth.get().getTwoFactorType().name(),
                        "Two-factor authentication required"
                ));
            }

            SecurityContextHolder.getContext().setAuthentication(authentication);
            String jwt = jwtUtils.generateJwtToken(authentication);

            RefreshToken refreshToken = refreshTokenService.createRefreshToken(userDetails.getId());

            List<String> roles = userDetails.getAuthorities().stream()
                    .map(item -> item.getAuthority())
                    .toList();

            return ResponseEntity.ok(new JwtResponse(jwt,
                    refreshToken.getToken(),
                    userDetails.getId(),
                    userDetails.getUsername(),
                    userDetails.getEmail(),
                    userDetails.getAvatarUrl(),
                    roles));
        } catch (BadCredentialsException ex) {
            throw new BadCredentialsException("Authorization failed. Please check that you have entered the correct details.");
        }
    }

    @PostMapping("/login/2fa")
    public ResponseEntity<JwtResponse> verifyTwoFactorLogin(@Valid @RequestBody TwoFactorLoginRequest request) throws RuntimeException {
        if (!jwtUtils.validateTempToken(request.getTempToken())) {
            throw new BadCredentialsException("Invalid or expired verification session");
        }

        Long userId = jwtUtils.getUserIdFromTempToken(request.getTempToken());
        String username = jwtUtils.getUsernameFromTempToken(request.getTempToken());

        if (!twoFactorAuthService.verifyLoginCode(userId, request.getCode())) {
            throw new BadCredentialsException("Invalid verification code");
        }

        // Generate full JWT token
        User user = userRepository.findById(userId)
                .orElseThrow(() -> new RuntimeException("User not found"));

        String jwt = jwtUtils.generateJwtTokenForUser(userId, username);

        RefreshToken refreshToken = refreshTokenService.createRefreshToken(userId);

        List<String> roles = List.of("ROLE_USER");

        return ResponseEntity.ok(new JwtResponse(jwt,
                refreshToken.getToken(),
                user.getId(),
                user.getUsername(),
                user.getEmail(),
                user.getAvatarUrl(),
                roles));

    }

    @PostMapping("/login/2fa/resend")
    public ResponseEntity<MessageResponse> resendTwoFactorCode(@RequestBody TwoFactorLoginRequest request) {
        if (!jwtUtils.validateTempToken(request.getTempToken())) {
            throw new BadCredentialsException("Invalid or expired verification session");
        }

        Long userId = jwtUtils.getUserIdFromTempToken(request.getTempToken());
        twoFactorAuthService.sendLoginEmailCode(userId);

        return ResponseEntity.ok(new MessageResponse("Verification code sent to your email"));
    }

    @PostMapping("/register")
    public ResponseEntity<JwtResponse> registerUser(@Valid @RequestBody SignupRequest signUpRequest) {
        if (Boolean.TRUE.equals(userRepository.existsByUsername(signUpRequest.getUsername()))) {
            throw new EntityExistsException("Username is already taken!");
        }

        if (Boolean.TRUE.equals(userRepository.existsByEmail(signUpRequest.getEmail()))) {
            throw new EntityExistsException("Error: Email is already in use!");
        }

        User user = new User(signUpRequest.getUsername(),
                signUpRequest.getEmail(),
                encoder.encode(signUpRequest.getPassword()),
                signUpRequest.getCompany());

        Set<String> strRoles = signUpRequest.getRole();
        Set<Role> roles = new HashSet<>();

        if (strRoles == null || strRoles.isEmpty()) {
            Role userRole = roleRepository.findByName(ERole.ROLE_USER)
                    .orElseThrow(() -> new RuntimeException("Error: Role is not found."));
            roles.add(userRole);
        } else {
            strRoles.forEach(role -> {
                switch (role) {
                    case "admin":
                        Role adminRole = roleRepository.findByName(ERole.ROLE_ADMIN)
                                .orElseThrow(() -> new RuntimeException("Error: Role is not found."));
                        roles.add(adminRole);

                        break;
                    case "mod":
                        Role modRole = roleRepository.findByName(ERole.ROLE_MODERATOR)
                                .orElseThrow(() -> new RuntimeException("Error: Role is not found."));
                        roles.add(modRole);

                        break;
                    default:
                        Role userRole = roleRepository.findByName(ERole.ROLE_USER)
                                .orElseThrow(() -> new RuntimeException("Error: Role is not found."));
                        roles.add(userRole);
                }
            });
        }

        user.setStatus(EStatus.STATUS_ACTIVE);
        user.setAccountType(EAccountType.TYPE_DEFAULT);
        userRepository.save(user);

        Authentication authentication = authenticationManager.authenticate(
                new UsernamePasswordAuthenticationToken(signUpRequest.getUsername(), signUpRequest.getPassword()));

        SecurityContextHolder.getContext().setAuthentication(authentication);
        String jwt = jwtUtils.generateJwtToken(authentication);

        UserDetailsImpl userDetails = (UserDetailsImpl) authentication.getPrincipal();
        
        RefreshToken refreshToken = refreshTokenService.createRefreshToken(userDetails.getId());
        
        List<String> userRoles = userDetails.getAuthorities().stream()
                .map(item -> item.getAuthority())
                .toList();

        return ResponseEntity.ok(new JwtResponse(jwt,
                refreshToken.getToken(),
                userDetails.getId(),
                userDetails.getUsername(),
                userDetails.getEmail(),
                userDetails.getAvatarUrl(),
                userRoles));
    }
    
    /**
     * Request password reset - sends email with reset link
     */
    @PostMapping("/forgot-password")
    public ResponseEntity<MessageResponse> forgotPassword(@Valid @RequestBody ForgotPasswordRequest request) {
        passwordResetService.requestPasswordReset(request.getEmail());
        return ResponseEntity.ok(new MessageResponse("If an account exists with this email, you will receive a password reset link."));
    }
    
    /**
     * Validate reset token and check if 2FA is required
     */
    @PostMapping("/validate-reset-token")
    public ResponseEntity<ResetTokenValidationResponse> validateResetToken(@Valid @RequestBody ValidateResetTokenRequest request) {
        ResetTokenValidationResponse response = passwordResetService.validateResetToken(request.getToken());
        return ResponseEntity.ok(response);
    }
    
    /**
     * Reset password with token and optional 2FA code
     */
    @PostMapping("/reset-password")
    public ResponseEntity<MessageResponse> resetPassword(@Valid @RequestBody ResetPasswordRequest request) {
        passwordResetService.resetPassword(request.getToken(), request.getNewPassword(), request.getTwoFactorCode());
        return ResponseEntity.ok(new MessageResponse("Password has been reset successfully. You can now log in with your new password."));
    }

    /**
     * Refresh access token using refresh token
     */
    @PostMapping("/refresh")
    public ResponseEntity<JwtResponse> refreshToken(@Valid @RequestBody RefreshTokenRequest request) throws RefreshFailedException {
        RefreshToken refreshToken = refreshTokenService.verifyRefreshToken(request.getRefreshToken());
        User user = refreshToken.getUser();

        String newAccessToken = refreshTokenService.generateNewAccessToken(refreshToken);

        List<String> roles = List.of("ROLE_USER");

        return ResponseEntity.ok(new JwtResponse(newAccessToken,
                refreshToken.getToken(),
                user.getId(),
                user.getUsername(),
                user.getEmail(),
                user.getAvatarUrl(),
                roles)
        );
    }

    /**
     * Logout - revoke refresh token
     */
    @PostMapping("/logout")
    public ResponseEntity<MessageResponse> logout(@Valid @RequestBody RefreshTokenRequest request) {
        refreshTokenService.revokeToken(request.getRefreshToken());
        return ResponseEntity.ok(new MessageResponse("Logged out successfully"));
    }
}