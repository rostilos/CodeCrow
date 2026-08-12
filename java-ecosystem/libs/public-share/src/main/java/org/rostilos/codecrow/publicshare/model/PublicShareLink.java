package org.rostilos.codecrow.publicshare.model;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Index;
import jakarta.persistence.PrePersist;
import jakarta.persistence.Table;
import jakarta.persistence.UniqueConstraint;

import java.time.OffsetDateTime;

@Entity
@Table(
        name = "public_share_link",
        uniqueConstraints = @UniqueConstraint(
                name = "uq_public_share_link_token_hash",
                columnNames = "token_hash"
        ),
        indexes = @Index(
                name = "idx_public_share_link_resource",
                columnList = "resource_type, resource_key"
        )
)
public class PublicShareLink {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(nullable = false, updatable = false)
    private Long id;

    @Column(name = "resource_type", nullable = false, length = 80, updatable = false)
    private String resourceType;

    @Column(name = "resource_key", nullable = false, length = 255, updatable = false)
    private String resourceKey;

    @Column(name = "token_hash", nullable = false, length = 64, updatable = false)
    private String tokenHash;

    @Column(name = "expires_at")
    private OffsetDateTime expiresAt;

    @Column(name = "revoked_at")
    private OffsetDateTime revokedAt;

    @Column(name = "created_at", nullable = false, updatable = false)
    private OffsetDateTime createdAt;

    protected PublicShareLink() {
    }

    public PublicShareLink(String resourceType,
                           String resourceKey,
                           String tokenHash,
                           OffsetDateTime expiresAt) {
        this.resourceType = resourceType;
        this.resourceKey = resourceKey;
        this.tokenHash = tokenHash;
        this.expiresAt = expiresAt;
    }

    @PrePersist
    void onCreate() {
        if (createdAt == null) {
            createdAt = OffsetDateTime.now();
        }
    }

    public boolean isActiveAt(OffsetDateTime now) {
        return revokedAt == null && (expiresAt == null || expiresAt.isAfter(now));
    }

    public void revoke(OffsetDateTime now) {
        if (revokedAt == null) {
            revokedAt = now;
        }
    }

    public Long getId() { return id; }
    public String getResourceType() { return resourceType; }
    public String getResourceKey() { return resourceKey; }
    public String getTokenHash() { return tokenHash; }
    public OffsetDateTime getExpiresAt() { return expiresAt; }
    public OffsetDateTime getRevokedAt() { return revokedAt; }
    public OffsetDateTime getCreatedAt() { return createdAt; }
}
