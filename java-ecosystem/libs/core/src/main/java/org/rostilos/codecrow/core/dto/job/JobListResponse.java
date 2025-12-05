package org.rostilos.codecrow.core.dto.job;

import java.util.List;

public record JobListResponse(
        List<JobDTO> jobs,
        int page,
        int pageSize,
        long totalElements,
        int totalPages
) {}
