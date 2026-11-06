package org.rostilos.codecrow.core.model.user;

import jakarta.persistence.*;
import java.time.Instant;

@Entity
@Table(name = "ide_authorization")
public class IdeAuthorization {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    @Column(name = "device_hash", nullable = false, unique = true, length = 64)
    private String deviceHash;
    @Column(name = "user_code", nullable = false, unique = true, length = 16)
    private String userCode;
    @Column(name = "workspace_slug", nullable = false, length = 128)
    private String workspaceSlug;
    @Column(name = "expires_at", nullable = false)
    private Instant expiresAt;
    @Column(nullable = false)
    private boolean approved;

    public String getDeviceHash() { return deviceHash; }
    public void setDeviceHash(String value) { deviceHash = value; }
    public String getUserCode() { return userCode; }
    public void setUserCode(String value) { userCode = value; }
    public String getWorkspaceSlug() { return workspaceSlug; }
    public void setWorkspaceSlug(String value) { workspaceSlug = value; }
    public Instant getExpiresAt() { return expiresAt; }
    public void setExpiresAt(Instant value) { expiresAt = value; }
    public boolean isApproved() { return approved; }
    public void setApproved(boolean value) { approved = value; }
}
