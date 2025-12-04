package org.rostilos.codecrow.core.model.user.twofactor;

import jakarta.persistence.*;
import org.rostilos.codecrow.core.model.user.User;
import org.springframework.data.annotation.CreatedDate;
import org.springframework.data.annotation.LastModifiedDate;
import org.springframework.data.jpa.domain.support.AuditingEntityListener;

import java.time.Instant;

@Entity
@Table(name = "two_factor_auth")
@EntityListeners(AuditingEntityListener.class)
public class TwoFactorAuth {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @OneToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "user_id", nullable = false, unique = true)
    private User user;

    @Enumerated(EnumType.STRING)
    @Column(name = "two_factor_type", nullable = false)
    private ETwoFactorType twoFactorType;

    @Column(name = "secret_key")
    private String secretKey;

    @Column(name = "is_enabled", nullable = false)
    private boolean enabled = false;

    @Column(name = "is_verified", nullable = false)
    private boolean verified = false;

    @Column(name = "backup_codes", length = 1000)
    private String backupCodes;

    @Column(name = "email_code")
    private String emailCode;

    @Column(name = "email_code_expires_at")
    private Instant emailCodeExpiresAt;

    @CreatedDate
    @Column(name = "created_at", nullable = false, updatable = false)
    private Instant createdAt;

    @LastModifiedDate
    @Column(name = "updated_at")
    private Instant updatedAt;

    public TwoFactorAuth() {
    }

    public TwoFactorAuth(User user, ETwoFactorType twoFactorType) {
        this.user = user;
        this.twoFactorType = twoFactorType;
    }

    public Long getId() {
        return id;
    }

    public void setId(Long id) {
        this.id = id;
    }

    public User getUser() {
        return user;
    }

    public void setUser(User user) {
        this.user = user;
    }

    public ETwoFactorType getTwoFactorType() {
        return twoFactorType;
    }

    public void setTwoFactorType(ETwoFactorType twoFactorType) {
        this.twoFactorType = twoFactorType;
    }

    public String getSecretKey() {
        return secretKey;
    }

    public void setSecretKey(String secretKey) {
        this.secretKey = secretKey;
    }

    public boolean isEnabled() {
        return enabled;
    }

    public void setEnabled(boolean enabled) {
        this.enabled = enabled;
    }

    public boolean isVerified() {
        return verified;
    }

    public void setVerified(boolean verified) {
        this.verified = verified;
    }

    public String getBackupCodes() {
        return backupCodes;
    }

    public void setBackupCodes(String backupCodes) {
        this.backupCodes = backupCodes;
    }

    public String getEmailCode() {
        return emailCode;
    }

    public void setEmailCode(String emailCode) {
        this.emailCode = emailCode;
    }

    public Instant getEmailCodeExpiresAt() {
        return emailCodeExpiresAt;
    }

    public void setEmailCodeExpiresAt(Instant emailCodeExpiresAt) {
        this.emailCodeExpiresAt = emailCodeExpiresAt;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }

    public Instant getUpdatedAt() {
        return updatedAt;
    }

    public boolean isEmailCodeExpired() {
        return emailCodeExpiresAt != null && Instant.now().isAfter(emailCodeExpiresAt);
    }
}
