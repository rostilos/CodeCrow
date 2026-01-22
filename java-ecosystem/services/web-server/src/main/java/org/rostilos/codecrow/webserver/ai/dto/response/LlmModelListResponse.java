package org.rostilos.codecrow.webserver.ai.dto.response;

import java.util.List;

public record LlmModelListResponse(
        List<LlmModelDTO> models,
        int page,
        int pageSize,
        long totalElements,
        int totalPages
) {}
