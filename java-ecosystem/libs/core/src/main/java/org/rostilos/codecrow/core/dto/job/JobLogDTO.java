package org.rostilos.codecrow.core.dto.job;

import org.rostilos.codecrow.core.model.job.JobLog;
import org.rostilos.codecrow.core.model.job.JobLogLevel;

import java.time.OffsetDateTime;

public record JobLogDTO(
        String id,
        Long sequenceNumber,
        JobLogLevel level,
        String step,
        String message,
        String metadata,
        Long durationMs,
        OffsetDateTime timestamp
) {
    /**
     * Create DTO from JobLog entity.
     */
    public static JobLogDTO from(JobLog log) {
        return new JobLogDTO(
                log.getExternalId(),
                log.getSequenceNumber(),
                log.getLevel(),
                log.getStep(),
                log.getMessage(),
                log.getMetadata(),
                log.getDurationMs(),
                log.getTimestamp()
        );
    }
}
