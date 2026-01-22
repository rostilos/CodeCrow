package org.rostilos.codecrow.webserver.workspace.service;

import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.workspace.WorkspaceMemberRepository;
import org.rostilos.codecrow.core.persistence.repository.workspace.WorkspaceRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.OffsetDateTime;
import java.util.List;

@Service
public class WorkspaceDeletionScheduler {

    private static final Logger logger = LoggerFactory.getLogger(WorkspaceDeletionScheduler.class);

    private final WorkspaceRepository workspaceRepository;
    private final WorkspaceMemberRepository workspaceMemberRepository;

    public WorkspaceDeletionScheduler(
            WorkspaceRepository workspaceRepository,
            WorkspaceMemberRepository workspaceMemberRepository) {
        this.workspaceRepository = workspaceRepository;
        this.workspaceMemberRepository = workspaceMemberRepository;
    }

    /**
     * Runs every hour to check for workspaces that have passed their scheduled deletion date.
     */
    @Scheduled(cron = "0 0 * * * *") // Every hour at minute 0
    @Transactional
    public void processScheduledDeletions() {
        OffsetDateTime now = OffsetDateTime.now();
        List<Workspace> workspacesToDelete = workspaceRepository
                .findByScheduledDeletionAtBeforeAndScheduledDeletionAtIsNotNull(now);

        for (Workspace workspace : workspacesToDelete) {
            try {
                logger.info("Permanently deleting workspace '{}' (id={}) - scheduled deletion date passed",
                        workspace.getSlug(), workspace.getId());

                // Delete all workspace members first
                workspaceMemberRepository.deleteAllByWorkspaceId(workspace.getId());

                // Delete the workspace
                workspaceRepository.delete(workspace);

                logger.info("Successfully deleted workspace '{}'", workspace.getSlug());
            } catch (Exception e) {
                logger.error("Failed to delete workspace '{}': {}", workspace.getSlug(), e.getMessage(), e);
            }
        }

        if (!workspacesToDelete.isEmpty()) {
            logger.info("Processed {} scheduled workspace deletions", workspacesToDelete.size());
        }
    }
}
