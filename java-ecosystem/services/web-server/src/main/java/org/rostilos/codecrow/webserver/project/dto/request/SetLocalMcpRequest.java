package org.rostilos.codecrow.webserver.project.dto.request;

import jakarta.validation.constraints.NotNull;

/**
 * Request DTO to toggle project-level useLocalMcp configuration.
 */
public class SetLocalMcpRequest {
    @NotNull
    private Boolean useLocalMcp;

    public SetLocalMcpRequest() {
    }

    public SetLocalMcpRequest(Boolean useLocalMcp) {
        this.useLocalMcp = useLocalMcp;
    }

    public Boolean getUseLocalMcp() {
        return useLocalMcp;
    }

    public void setUseLocalMcp(Boolean useLocalMcp) {
        this.useLocalMcp = useLocalMcp;
    }
}
