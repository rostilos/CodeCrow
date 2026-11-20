package org.rostilos.codecrow.core.service;

import org.rostilos.codecrow.core.model.user.PersonalAccessToken;
import org.rostilos.codecrow.core.model.user.status.EStatus;
import org.rostilos.codecrow.core.model.workspace.EMembershipStatus;
import org.rostilos.codecrow.core.persistence.repository.user.PersonalAccessTokenRepository;
import org.rostilos.codecrow.core.persistence.repository.workspace.WorkspaceMemberRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.Base64;
import java.util.HexFormat;
import java.util.List;
import java.util.Optional;

@Service
public class PersonalAccessTokenService {
    public static final String PREFIX = "ccpat_";
    private static final SecureRandom RANDOM = new SecureRandom();
    private final PersonalAccessTokenRepository tokens;
    private final WorkspaceMemberRepository members;

    public PersonalAccessTokenService(PersonalAccessTokenRepository tokens, WorkspaceMemberRepository members) {
        this.tokens = tokens;
        this.members = members;
    }

    public static String secret() {
        byte[] bytes = new byte[32];
        RANDOM.nextBytes(bytes);
        return PREFIX + Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
    }

    public static String hash(String secret) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(secret.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException e) { throw new IllegalStateException(e); }
    }

    @Transactional
    public PersonalAccessToken create(Long userId, Long workspaceId, String name, boolean write, int days, String hash) {
        if (name == null || name.isBlank() || name.length() > 100 || days < 1 || days > 365) {
            throw new IllegalArgumentException("Provide a name (1–100 characters) and expiry of 1–365 days.");
        }
        var member = members.findByWorkspaceIdAndUserId(workspaceId, userId)
                .filter(m -> m.getStatus() == EMembershipStatus.ACTIVE && m.getUser().getStatus() == EStatus.STATUS_ACTIVE)
                .orElseThrow(() -> new SecurityException("Active workspace membership is required."));
        var token = new PersonalAccessToken();
        token.setUser(member.getUser());
        token.setWorkspace(member.getWorkspace());
        token.setName(name.trim());
        token.setWriteAllowed(write);
        token.setTokenHash(hash);
        token.setExpiresAt(Instant.now().plus(days, ChronoUnit.DAYS));
        return tokens.save(token);
    }

    @Transactional(readOnly = true)
    public Optional<PersonalAccessToken> authenticate(String secret) {
        if (secret == null || !secret.startsWith(PREFIX) || secret.length() != PREFIX.length() + 43) return Optional.empty();
        return tokens.findByTokenHash(hash(secret))
                .filter(t -> t.getExpiresAt().isAfter(Instant.now()) && t.getUser().getStatus() == EStatus.STATUS_ACTIVE)
                .filter(t -> members.findByWorkspaceIdAndUserId(t.getWorkspace().getId(), t.getUser().getId())
                        .filter(m -> m.getStatus() == EMembershipStatus.ACTIVE).isPresent());
    }

    public static boolean permits(PersonalAccessToken token, String method, String workspaceSlug) {
        if (!token.getWorkspace().getSlug().equals(workspaceSlug)) return false;
        return List.of("GET", "HEAD", "OPTIONS").contains(method) || token.isWriteAllowed();
    }
}
