package org.rostilos.codecrow.webserver.auth.controller;

import org.rostilos.codecrow.core.model.user.PersonalAccessToken;
import org.rostilos.codecrow.core.persistence.repository.user.PersonalAccessTokenRepository;
import org.rostilos.codecrow.core.service.PersonalAccessTokenService;
import org.rostilos.codecrow.security.annotations.IsWorkspaceMember;
import org.rostilos.codecrow.security.service.UserDetailsImpl;
import org.rostilos.codecrow.webserver.workspace.service.WorkspaceService;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.NoSuchElementException;

@RestController
@IsWorkspaceMember
@RequestMapping("/api/{workspaceSlug}/developer-tokens")
public class PersonalTokenController {
    public record Create(String name, boolean write, int days) {}
    public record TokenInfo(Long id, String name, List<String> scopes, Instant createdAt, Instant expiresAt) {
        static TokenInfo from(PersonalAccessToken token) {
            return new TokenInfo(token.getId(), token.getName(), token.isWriteAllowed() ? List.of("read", "write") : List.of("read"),
                    token.getCreatedAt(), token.getExpiresAt());
        }
    }
    private final PersonalAccessTokenService service;
    private final PersonalAccessTokenRepository tokens;
    private final WorkspaceService workspaces;

    public PersonalTokenController(PersonalAccessTokenService service, PersonalAccessTokenRepository tokens, WorkspaceService workspaces) {
        this.service = service; this.tokens = tokens; this.workspaces = workspaces;
    }

    @GetMapping
    public List<TokenInfo> list(@PathVariable String workspaceSlug, @AuthenticationPrincipal UserDetailsImpl user) {
        return tokens.findByUserIdAndWorkspaceIdOrderByCreatedAtDesc(user.getId(), workspaces.getWorkspaceBySlug(workspaceSlug).getId())
                .stream().map(TokenInfo::from).toList();
    }

    @PostMapping
    public Map<String, Object> create(@PathVariable String workspaceSlug, @AuthenticationPrincipal UserDetailsImpl user,
                                      @RequestBody Create request) {
        String secret = PersonalAccessTokenService.secret();
        var token = service.create(user.getId(), workspaces.getWorkspaceBySlug(workspaceSlug).getId(),
                request.name(), request.write(), request.days(), PersonalAccessTokenService.hash(secret));
        return Map.of("token", secret, "details", TokenInfo.from(token));
    }

    @DeleteMapping("/{tokenId}")
    public void revoke(@PathVariable String workspaceSlug, @PathVariable Long tokenId, @AuthenticationPrincipal UserDetailsImpl user) {
        var token = tokens.findByIdAndUserIdAndWorkspaceId(tokenId, user.getId(), workspaces.getWorkspaceBySlug(workspaceSlug).getId())
                .orElseThrow(() -> new NoSuchElementException("Token not found."));
        tokens.delete(token);
    }
}
