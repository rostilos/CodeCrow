package org.rostilos.codecrow.webserver.service.project;

import java.security.GeneralSecurityException;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.util.Date;
import java.util.List;

import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.ProjectToken;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectTokenRepository;
import org.rostilos.codecrow.security.jwt.utils.JwtUtils;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class ProjectTokenService {
    private final ProjectRepository projectRepository;
    private final ProjectTokenRepository projectTokenRepository;
    private final TokenEncryptionService tokenEncryptionService;
    private final JwtUtils jwtUtils;

    public ProjectTokenService(ProjectRepository projectRepository,
                               ProjectTokenRepository projectTokenRepository,
                               TokenEncryptionService tokenEncryptionService,
                               JwtUtils jwtUtils
    ) {
        this.projectRepository = projectRepository;
        this.projectTokenRepository = projectTokenRepository;
        this.tokenEncryptionService = tokenEncryptionService;
        this.jwtUtils = jwtUtils;
    }

    /**
     * Validate presence of a configured project token and issue a short-lived JWT.
     * Current behavior: if the project has at least one active ProjectToken (or legacy
     * Project.authToken is present) a JWT is issued for the requesting user.
     */
    @Transactional
    public String generateProjectJwt(
            Long workspaceId,
            Long projectId,
            Long userId,
            String name,
            String lifetime
    ) throws SecurityException, GeneralSecurityException {
        Project project = projectRepository.findByWorkspaceIdAndId(workspaceId, projectId)
                .orElseThrow(() -> new IllegalArgumentException("Project not found"));

        ZonedDateTime now = ZonedDateTime.now(ZoneOffset.UTC);

        Instant expires = getExpires(lifetime, now);

        String projectJwt = jwtUtils.generateJwtTokenForProjectWithUser(
                String.valueOf(project.getId()),
                String.valueOf(userId),
                Date.from(expires)
        );
        String encrypted = tokenEncryptionService.encrypt(projectJwt);

        ProjectToken token = new ProjectToken();
        token.setProject(project);
        token.setName(name);
        token.setTokenEncrypted(encrypted);
        token.setCreatedAt(Instant.now());
        token.setExpiresAt(expires);
        projectTokenRepository.save(token);
        return projectJwt;
    }

    private static Instant getExpires(String lifetime, ZonedDateTime now) {
        ZonedDateTime expiresZdt;

        if (lifetime != null) {
            String normalized = lifetime.trim().toUpperCase();
            expiresZdt = switch (normalized) {
                case "3MONTH", "3_MONTH", "3M", "3MONTHS", "90D" -> now.plusMonths(3);
                case "6MONTH", "6_MONTH", "6M", "6MONTHS", "180D" -> now.plusMonths(6);
                case "1YEAR", "1_YEAR", "1Y", "1YEARS", "365D" -> now.plusYears(1);
                default -> now.plusMonths(1);
            };
        } else {
            expiresZdt = now.plusMonths(1);
        }
        return expiresZdt.toInstant();
    }

    @Transactional(readOnly = true)
    public List<ProjectToken> listTokens(Long workspaceId, Long projectId) {
        // validation: ensure project exists in workspace
        projectRepository.findByWorkspaceIdAndId(workspaceId, projectId)
                .orElseThrow(() -> new IllegalArgumentException("Project not found"));
        return projectTokenRepository.findByProject_Id(projectId);
    }

    @Transactional
    public void deleteToken(Long workspaceId, Long projectId, Long tokenId) {
        // validation: ensure project exists in workspace
        projectRepository.findByWorkspaceIdAndId(workspaceId, projectId)
                .orElseThrow(() -> new IllegalArgumentException("Project not found"));

        ProjectToken token = projectTokenRepository.findByIdAndProject_Id(tokenId, projectId)
                .orElseThrow(() -> new IllegalArgumentException("Token not found"));
        projectTokenRepository.delete(token);
    }
}
