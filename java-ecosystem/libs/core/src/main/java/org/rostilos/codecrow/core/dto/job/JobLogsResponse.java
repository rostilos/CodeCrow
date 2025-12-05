package org.rostilos.codecrow.core.dto.job;

import java.util.List;

public record JobLogsResponse(
        String jobId,
        List<JobLogDTO> logs,
        Long latestSequence,
        boolean isComplete
) {}
