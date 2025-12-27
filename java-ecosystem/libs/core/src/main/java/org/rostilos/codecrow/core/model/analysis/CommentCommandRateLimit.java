package org.rostilos.codecrow.core.model.analysis;

import jakarta.persistence.*;
import org.rostilos.codecrow.core.model.project.Project;

import java.time.OffsetDateTime;

/**
 * Tracks rate limiting for comment commands at the project level.
 * Uses a sliding window approach to limit the number of commands per time period.
 */
@Entity
@Table(name = "comment_command_rate_limit",
        indexes = {
                @Index(name = "idx_rate_limit_project_window", columnList = "project_id, window_start")
        },
        uniqueConstraints = {
                @UniqueConstraint(name = "uk_rate_limit_project_window", columnNames = {"project_id", "window_start"})
        })
public class CommentCommandRateLimit {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(nullable = false, updatable = false)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "project_id", nullable = false)
    private Project project;

    @Column(name = "window_start", nullable = false)
    private OffsetDateTime windowStart;

    @Column(name = "command_count", nullable = false)
    private int commandCount = 0;

    @Column(name = "last_command_at")
    private OffsetDateTime lastCommandAt;

    public CommentCommandRateLimit() {
    }

    public CommentCommandRateLimit(Project project, OffsetDateTime windowStart) {
        this.project = project;
        this.windowStart = windowStart;
        this.commandCount = 0;
    }

    public Long getId() {
        return id;
    }

    public Project getProject() {
        return project;
    }

    public void setProject(Project project) {
        this.project = project;
    }

    public OffsetDateTime getWindowStart() {
        return windowStart;
    }

    public void setWindowStart(OffsetDateTime windowStart) {
        this.windowStart = windowStart;
    }

    public int getCommandCount() {
        return commandCount;
    }

    public void setCommandCount(int commandCount) {
        this.commandCount = commandCount;
    }

    public void incrementCommandCount() {
        this.commandCount++;
        this.lastCommandAt = OffsetDateTime.now();
    }

    public OffsetDateTime getLastCommandAt() {
        return lastCommandAt;
    }

    public void setLastCommandAt(OffsetDateTime lastCommandAt) {
        this.lastCommandAt = lastCommandAt;
    }
}
