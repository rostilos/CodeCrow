package org.rostilos.codecrow.webserver.controller.auth;

import jakarta.validation.Valid;
import org.rostilos.codecrow.webserver.dto.message.ErrorMessageResponse;
import org.rostilos.codecrow.webserver.dto.request.auth.GoogleAuthRequest;
import org.rostilos.codecrow.webserver.dto.response.auth.JwtResponse;
import org.rostilos.codecrow.webserver.service.auth.GoogleOAuthService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@CrossOrigin(origins = "*", maxAge = 3600)
@RestController
@RequestMapping("/api/auth")
public class GoogleAuthController {
    private static final Logger logger = LoggerFactory.getLogger(GoogleAuthController.class);

    private final GoogleOAuthService googleOAuthService;

    public GoogleAuthController(GoogleOAuthService googleOAuthService) {
        this.googleOAuthService = googleOAuthService;
    }

    @PostMapping("/google")
    public ResponseEntity<JwtResponse> authenticateWithGoogle(@Valid @RequestBody GoogleAuthRequest request) throws Exception {
        JwtResponse response = googleOAuthService.authenticateWithGoogle(request.getCredential());
        return ResponseEntity.ok(response);
    }
}
