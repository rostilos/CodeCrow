package org.rostilos.codecrow.events.notification;

import org.rostilos.codecrow.events.CodecrowEvent;

/**
 * Event fired when workspace ownership is being transferred.
 */
public class WorkspaceOwnershipTransferEvent extends CodecrowEvent {

    private final Long workspaceId;
    private final String workspaceName;
    private final Long fromUserId;
    private final String fromUserEmail;
    private final Long toUserId;
    private final String toUserEmail;

    public WorkspaceOwnershipTransferEvent(Object source, Long workspaceId, String workspaceName,
                                            Long fromUserId, String fromUserEmail,
                                            Long toUserId, String toUserEmail) {
        super(source);
        this.workspaceId = workspaceId;
        this.workspaceName = workspaceName;
        this.fromUserId = fromUserId;
        this.fromUserEmail = fromUserEmail;
        this.toUserId = toUserId;
        this.toUserEmail = toUserEmail;
    }

    @Override
    public String getEventType() {
        return "WORKSPACE_OWNERSHIP_TRANSFER";
    }

    public Long getWorkspaceId() {
        return workspaceId;
    }

    public String getWorkspaceName() {
        return workspaceName;
    }

    public Long getFromUserId() {
        return fromUserId;
    }

    public String getFromUserEmail() {
        return fromUserEmail;
    }

    public Long getToUserId() {
        return toUserId;
    }

    public String getToUserEmail() {
        return toUserEmail;
    }
}
