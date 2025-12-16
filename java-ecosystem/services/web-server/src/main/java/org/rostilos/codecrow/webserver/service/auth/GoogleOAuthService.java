package org.rostilos.codecrow.webserver.service.auth;

import com.google.api.client.googleapis.auth.oauth2.GoogleIdToken;
import com.google.api.client.googleapis.auth.oauth2.GoogleIdTokenVerifier;
import com.google.api.client.http.javanet.NetHttpTransport;
import com.google.api.client.json.gson.GsonFactory;
import org.rostilos.codecrow.core.model.user.RefreshToken;
import org.rostilos.codecrow.core.model.user.User;
import org.rostilos.codecrow.core.model.user.account_type.EAccountType;
import org.rostilos.codecrow.core.model.user.status.EStatus;
import org.rostilos.codecrow.core.persistence.repository.user.UserRepository;
import org.rostilos.codecrow.security.jwt.utils.JwtUtils;
import org.rostilos.codecrow.security.service.UserDetailsImpl;
import org.rostilos.codecrow.webserver.dto.response.auth.JwtResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Service;

import java.util.Collections;
import java.util.List;
import java.util.Optional;

@Service
public class GoogleOAuthService {
    private static final Logger logger = LoggerFactory.getLogger(GoogleOAuthService.class);

    @Value("${codecrow.oauth.google.client-id}")
    private String googleClientId;

    private final UserRepository userRepository;
    private final JwtUtils jwtUtils;
    private final RefreshTokenService refreshTokenService;

    public GoogleOAuthService(UserRepository userRepository, JwtUtils jwtUtils, RefreshTokenService refreshTokenService) {
        this.userRepository = userRepository;
        this.jwtUtils = jwtUtils;
        this.refreshTokenService = refreshTokenService;
    }

    public JwtResponse authenticateWithGoogle(String credential) throws Exception {
        GoogleIdToken.Payload payload = verifyGoogleToken(credential);
        
        String googleId = payload.getSubject();
        String email = payload.getEmail();
        String name = (String) payload.get("name");
        String picture = (String) payload.get("picture");

        // Find existing user by Google ID or email
        Optional<User> existingUser = userRepository.findByGoogleId(googleId);
        User user;

        if (existingUser.isPresent()) {
            user = existingUser.get();
            if (picture != null && !picture.equals(user.getAvatarUrl())) {
                user.setAvatarUrl(picture);
                userRepository.save(user);
            }
        } else {
            // Check if user with this email exists (link Google account)
            Optional<User> emailUserOpt = userRepository.findByEmail(email);
            if (emailUserOpt.isPresent()) {
                User emailUser = emailUserOpt.get();
                emailUser.setGoogleId(googleId);
                if (picture != null) {
                    emailUser.setAvatarUrl(picture);
                }
                user = userRepository.save(emailUser);
            } else {
                user = createUserFromGoogle(googleId, email, name, picture);
            }
        }

        return generateJwtResponse(user);
    }

    private GoogleIdToken.Payload verifyGoogleToken(String credential) throws Exception {
        GoogleIdTokenVerifier verifier = new GoogleIdTokenVerifier.Builder(
                new NetHttpTransport(),
                GsonFactory.getDefaultInstance())
                .setAudience(Collections.singletonList(googleClientId))
                .build();

        GoogleIdToken idToken = verifier.verify(credential);
        if (idToken == null) {
            throw new IllegalArgumentException("Invalid Google token");
        }

        GoogleIdToken.Payload payload = idToken.getPayload();
        
        if (!payload.getEmailVerified()) {
            throw new IllegalArgumentException("Email not verified by Google");
        }

        return payload;
    }

    private User createUserFromGoogle(String googleId, String email, String name, String picture) {
        User user = new User();
        user.setGoogleId(googleId);
        user.setEmail(email);
        user.setUsername(generateUniqueUsername(email, name));
        user.setAvatarUrl(picture);
        user.setStatus(EStatus.STATUS_ACTIVE);
        user.setAccountType(EAccountType.TYPE_DEFAULT);
        // No password for Google OAuth users

        return userRepository.save(user);
    }

    private String generateUniqueUsername(String email, String name) {
        String baseUsername;
        if (name != null && !name.isBlank()) {
            baseUsername = name.toLowerCase().replaceAll("[^a-z0-9]", "");
        } else {
            baseUsername = email.split("@")[0].toLowerCase().replaceAll("[^a-z0-9]", "");
        }

        if (baseUsername.length() < 2) {
            baseUsername = "user";
        }

        if (baseUsername.length() > 16) {
            baseUsername = baseUsername.substring(0, 16);
        }

        String username = baseUsername;
        int suffix = 1;
        while (Boolean.TRUE.equals(userRepository.existsByUsername(username))) {
            username = baseUsername + suffix;
            suffix++;
        }

        return username;
    }

    private JwtResponse generateJwtResponse(User user) {
        List<SimpleGrantedAuthority> authorities = Collections.singletonList(
                new SimpleGrantedAuthority("ROLE_USER")
        );

        UserDetailsImpl userDetails = UserDetailsImpl.build(user);
        
        Authentication authentication = new UsernamePasswordAuthenticationToken(
                userDetails, null, authorities);
        SecurityContextHolder.getContext().setAuthentication(authentication);

        String jwt = jwtUtils.generateJwtToken(authentication);
        
        // Create refresh token
        RefreshToken refreshToken = refreshTokenService.createRefreshToken(user.getId());

        List<String> roles = authorities.stream()
                .map(SimpleGrantedAuthority::getAuthority)
                .toList();

        return new JwtResponse(jwt, refreshToken.getToken(), user.getId(), user.getUsername(), user.getEmail(), user.getAvatarUrl(), roles);
    }
}
