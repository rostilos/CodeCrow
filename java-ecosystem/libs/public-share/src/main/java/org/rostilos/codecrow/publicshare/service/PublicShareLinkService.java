package org.rostilos.codecrow.publicshare.service;

import org.rostilos.codecrow.publicshare.api.IssuedPublicShare;
import org.rostilos.codecrow.publicshare.api.ResolvedPublicShare;
import org.rostilos.codecrow.publicshare.model.PublicShareLink;
import org.rostilos.codecrow.publicshare.persistence.PublicShareLinkRepository;
import org.springframework.transaction.annotation.Transactional;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.time.OffsetDateTime;
import java.util.Base64;
import java.util.HexFormat;
import java.util.Optional;

/**
 * Issues and resolves opaque public-share credentials.
 *
 * <p>The token is deliberately unrelated to application JWTs: it contains no
 * claims or identifiers, is generated from 256 bits of randomness, and only a
 * one-way SHA-256 hash is stored.</p>
 */
@Transactional
public class PublicShareLinkService {

    private static final String TOKEN_PREFIX = "ccs_";
    private static final int TOKEN_BYTES = 32;

    private final PublicShareLinkRepository repository;
    private final SecureRandom secureRandom;

    public PublicShareLinkService(PublicShareLinkRepository repository) {
        this(repository, new SecureRandom());
    }

    PublicShareLinkService(PublicShareLinkRepository repository, SecureRandom secureRandom) {
        this.repository = repository;
        this.secureRandom = secureRandom;
    }

    public IssuedPublicShare issue(String resourceType, String resourceKey) {
        return issue(resourceType, resourceKey, null);
    }

    public IssuedPublicShare issue(String resourceType,
                                   String resourceKey,
                                   OffsetDateTime expiresAt) {
        String normalizedType = requireValue(resourceType, "Resource type", 80);
        String normalizedKey = requireValue(resourceKey, "Resource key", 255);
        if (expiresAt != null && !expiresAt.isAfter(OffsetDateTime.now())) {
            throw new IllegalArgumentException("Public share expiry must be in the future.");
        }

        byte[] randomBytes = new byte[TOKEN_BYTES];
        secureRandom.nextBytes(randomBytes);
        String token = TOKEN_PREFIX + Base64.getUrlEncoder().withoutPadding().encodeToString(randomBytes);

        repository.save(new PublicShareLink(
                normalizedType,
                normalizedKey,
                hash(token),
                expiresAt
        ));
        return new IssuedPublicShare(token);
    }

    @Transactional(readOnly = true)
    public Optional<ResolvedPublicShare> resolve(String token) {
        if (!hasValidShape(token)) {
            return Optional.empty();
        }
        OffsetDateTime now = OffsetDateTime.now();
        return repository.findByTokenHash(hash(token))
                .filter(link -> link.isActiveAt(now))
                .map(link -> new ResolvedPublicShare(
                        link.getResourceType(),
                        link.getResourceKey()
                ));
    }

    public boolean revoke(String token) {
        if (!hasValidShape(token)) {
            return false;
        }
        Optional<PublicShareLink> link = repository.findByTokenHash(hash(token));
        link.ifPresent(value -> value.revoke(OffsetDateTime.now()));
        return link.isPresent();
    }

    private static boolean hasValidShape(String token) {
        return token != null
                && token.startsWith(TOKEN_PREFIX)
                && token.length() >= TOKEN_PREFIX.length() + 40
                && token.length() <= 128;
    }

    private static String requireValue(String value, String fieldName, int maxLength) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(fieldName + " is required.");
        }
        String normalized = value.trim();
        if (normalized.length() > maxLength) {
            throw new IllegalArgumentException(fieldName + " is too long.");
        }
        return normalized;
    }

    static String hash(String token) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            return HexFormat.of().formatHex(digest.digest(token.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 is unavailable", e);
        }
    }
}
