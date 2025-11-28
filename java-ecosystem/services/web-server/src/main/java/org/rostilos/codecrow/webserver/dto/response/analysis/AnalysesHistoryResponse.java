

package org.rostilos.codecrow.webserver.dto.response.analysis;

import org.rostilos.codecrow.core.dto.analysis.AnalysisItemDTO;

import java.util.List;
import java.util.ArrayList;

public record AnalysesHistoryResponse(
        List<AnalysisItemDTO> analyses
) {
    public AnalysesHistoryResponse() {
        this(new ArrayList<>());
    }
}