package org.rostilos.codecrow.webserver.auth.service;

import org.rostilos.codecrow.core.model.user.IdeAuthorization;
import org.rostilos.codecrow.core.persistence.repository.user.IdeAuthorizationRepository;
import org.rostilos.codecrow.core.persistence.repository.workspace.WorkspaceRepository;
import org.rostilos.codecrow.core.service.PersonalAccessTokenService;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;
import java.time.Instant;
import java.util.Map;
import java.util.UUID;

@Service
public class IdeAuthorizationService {
    private final IdeAuthorizationRepository requests;
    private final WorkspaceRepository workspaces;
    private final PersonalAccessTokenService tokens;

    public IdeAuthorizationService(IdeAuthorizationRepository requests, WorkspaceRepository workspaces, PersonalAccessTokenService tokens) {
        this.requests = requests; this.workspaces = workspaces; this.tokens = tokens;
    }

    @Transactional
    public Map<String, Object> start(String workspace) {
        if (workspace == null || !workspace.matches("[A-Za-z0-9][A-Za-z0-9_-]{0,127}")) {
            throw new IllegalArgumentException("Workspace slug is required.");
        }
        requests.deleteExpired(Instant.now());
        String deviceCode = PersonalAccessTokenService.secret();
        var request = new IdeAuthorization();
        request.setDeviceHash(PersonalAccessTokenService.hash(deviceCode));
        request.setUserCode(UUID.randomUUID().toString().replace("-", "").substring(0, 12).toUpperCase());
        request.setWorkspaceSlug(workspace);
        request.setExpiresAt(Instant.now().plusSeconds(600));
        requests.save(request);
        return Map.of("deviceCode", deviceCode, "userCode", request.getUserCode(), "expiresIn", 600, "interval", 5);
    }

    @Transactional(readOnly = true)
    public Map<String, String> poll(String deviceCode) {
        if (deviceCode == null || deviceCode.length() > 100) throw new IllegalArgumentException("Device code is required.");
        var request = requests.findByDeviceHash(PersonalAccessTokenService.hash(deviceCode))
                .filter(r -> r.getExpiresAt().isAfter(Instant.now()))
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.GONE, "Authorization expired or was denied."));
        if (!request.isApproved()) return Map.of("status", "pending");
        return Map.of("status", tokens.authenticate(deviceCode).isPresent() ? "approved" : "revoked");
    }

    @Transactional
    public void approve(Long userId, String userCode, String workspaceSlug, boolean approve, boolean write, int days) {
        if (userId == null) throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "Sign in to authorize your IDE.");
        var request = requests.findForApproval(userCode)
                .filter(r -> r.getExpiresAt().isAfter(Instant.now()) && !r.isApproved())
                .filter(r -> r.getWorkspaceSlug().equals(workspaceSlug))
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.GONE, "Authorization expired, was already used, or the workspace does not match."));
        var workspace = workspaces.findBySlug(workspaceSlug)
                .orElseThrow(() -> new SecurityException("Active workspace membership is required."));
        if (!approve) { requests.delete(request); return; }
        tokens.create(userId, workspace.getId(), "IDE connection", write, days, request.getDeviceHash());
        request.setApproved(true);
        requests.save(request);
    }
}
