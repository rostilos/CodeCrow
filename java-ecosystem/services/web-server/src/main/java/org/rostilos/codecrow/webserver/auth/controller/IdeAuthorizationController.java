package org.rostilos.codecrow.webserver.auth.controller;

import org.rostilos.codecrow.security.service.UserDetailsImpl;
import org.rostilos.codecrow.webserver.auth.service.IdeAuthorizationService;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;
import java.util.Map;

@RestController
@RequestMapping("/api/auth/ide")
public class IdeAuthorizationController {
    public record Start(String workspaceSlug) {}
    public record Poll(String deviceCode) {}
    public record Approve(String userCode, String workspaceSlug, boolean approve, boolean write, int days) {}
    private final IdeAuthorizationService service;
    public IdeAuthorizationController(IdeAuthorizationService service) { this.service = service; }

    @PostMapping("/start")
    public ResponseEntity<Map<String, Object>> start(@RequestBody Start request) {
        return ResponseEntity.ok().cacheControl(CacheControl.noStore()).body(service.start(request.workspaceSlug()));
    }
    @PostMapping("/poll")
    public ResponseEntity<Map<String, String>> poll(@RequestBody Poll request) {
        return ResponseEntity.ok().cacheControl(CacheControl.noStore()).body(service.poll(request.deviceCode()));
    }
    @PostMapping("/approve")
    public void approve(@AuthenticationPrincipal UserDetailsImpl user, @RequestBody Approve request) {
        service.approve(user == null ? null : user.getId(), request.userCode(), request.workspaceSlug(),
                request.approve(), request.write(), request.days());
    }
}
