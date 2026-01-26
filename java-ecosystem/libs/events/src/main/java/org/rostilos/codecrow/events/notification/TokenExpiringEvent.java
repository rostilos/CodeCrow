package org.rostilos.codecrow.events.notification;

import org.rostilos.codecrow.events.CodecrowEvent;

import java.time.OffsetDateTime;

/**
 * Event fired when a VCS connection token is approaching expiration.
 */
public class TokenExpiringEvent extends CodecrowEvent {

    private final Long workspaceId;
    private final Long vcsConnectionId;
    private final String vcsProvider;
    private final OffsetDateTime expiresAt;
    private final int daysUntilExpiry;

    public TokenExpiringEvent(Object source, Long workspaceId, Long vcsConnectionId,
                              String vcsProvider, OffsetDateTime expiresAt, int daysUntilExpiry) {
        super(source);
        this.workspaceId = workspaceId;
        this.vcsConnectionId = vcsConnectionId;
        this.vcsProvider = vcsProvider;
        this.expiresAt = expiresAt;
        this.daysUntilExpiry = daysUntilExpiry;
    }

    @Override
    public String getEventType() {
        return "TOKEN_EXPIRING";
    }

    public Long getWorkspaceId() {
        return workspaceId;
    }

    public Long getVcsConnectionId() {
        return vcsConnectionId;
    }

    public String getVcsProvider() {
        return vcsProvider;
    }

    public OffsetDateTime getExpiresAt() {
        return expiresAt;
    }

    public int getDaysUntilExpiry() {
        return daysUntilExpiry;
    }

    public boolean isExpired() {
        return daysUntilExpiry <= 0;
    }
}
