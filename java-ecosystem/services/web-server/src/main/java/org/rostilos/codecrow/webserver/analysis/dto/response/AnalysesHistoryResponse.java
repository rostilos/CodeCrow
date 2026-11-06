

package org.rostilos.codecrow.webserver.analysis.dto.response;

import org.rostilos.codecrow.core.dto.analysis.AnalysisItemDTO;

import java.util.List;
import java.util.ArrayList;

public record AnalysesHistoryResponse(
        List<AnalysisItemDTO> analyses,
        long totalElements,
        int totalPages,
        int currentPage,
        int pageSize
) {
    public AnalysesHistoryResponse() {
        this(new ArrayList<>(), 0, 0, 1, 20);
    }
}
