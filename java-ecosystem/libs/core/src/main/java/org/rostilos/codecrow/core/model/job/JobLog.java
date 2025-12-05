package org.rostilos.codecrow.core.model.job;

import jakarta.persistence.*;
import java.time.OffsetDateTime;
import java.util.UUID;

/**
 * Represents a single log entry for a job.
 * Logs are stored in the database for persistence and can be streamed via SSE.
 */
@Entity
@Table(name = "job_log", indexes = {
        @Index(name = "idx_job_log_job_id", columnList = "job_id"),
        @Index(name = "idx_job_log_timestamp", columnList = "timestamp"),
        @Index(name = "idx_job_log_level", columnList = "level"),
        @Index(name = "idx_job_log_sequence", columnList = "job_id, sequence_number")
})
public class JobLog {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(nullable = false, updatable = false)
    private Long id;

    /**
     * UUID for external reference.
     */
    @Column(name = "external_id", nullable = false, unique = true, length = 36)
    private String externalId = UUID.randomUUID().toString();

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "job_id", nullable = false)
    private Job job;

    /**
     * Sequence number for ordering within a job (auto-incremented per job).
     */
    @Column(name = "sequence_number", nullable = false)
    private Long sequenceNumber;

    @Column(name = "level", nullable = false, length = 10)
    @Enumerated(EnumType.STRING)
    private JobLogLevel level = JobLogLevel.INFO;

    /**
     * Current step/phase when log was created.
     */
    @Column(name = "step", length = 100)
    private String step;

    /**
     * Log message content.
     */
    @Column(name = "message", nullable = false, columnDefinition = "TEXT")
    private String message;

    /**
     * Additional structured data (JSON).
     */
    @Column(name = "metadata", columnDefinition = "TEXT")
    private String metadata;

    /**
     * Duration in milliseconds (for step completion logs).
     */
    @Column(name = "duration_ms")
    private Long durationMs;

    @Column(name = "timestamp", nullable = false, updatable = false)
    private OffsetDateTime timestamp = OffsetDateTime.now();

    // Getters and Setters
    public Long getId() { return id; }

    public String getExternalId() { return externalId; }
    public void setExternalId(String externalId) { this.externalId = externalId; }

    public Job getJob() { return job; }
    public void setJob(Job job) { this.job = job; }

    public Long getSequenceNumber() { return sequenceNumber; }
    public void setSequenceNumber(Long sequenceNumber) { this.sequenceNumber = sequenceNumber; }

    public JobLogLevel getLevel() { return level; }
    public void setLevel(JobLogLevel level) { this.level = level; }

    public String getStep() { return step; }
    public void setStep(String step) { this.step = step; }

    public String getMessage() { return message; }
    public void setMessage(String message) { this.message = message; }

    public String getMetadata() { return metadata; }
    public void setMetadata(String metadata) { this.metadata = metadata; }

    public Long getDurationMs() { return durationMs; }
    public void setDurationMs(Long durationMs) { this.durationMs = durationMs; }

    public OffsetDateTime getTimestamp() { return timestamp; }
}
