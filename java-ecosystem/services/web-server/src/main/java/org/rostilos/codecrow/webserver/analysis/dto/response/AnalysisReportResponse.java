package org.rostilos.codecrow.webserver.analysis.dto.response;

import org.rostilos.codecrow.core.dto.analysis.AnalysisItemDTO;
import org.rostilos.codecrow.core.dto.analysis.issue.IssueDTO;

import java.util.List;

public record AnalysisReportResponse(AnalysisItemDTO analysis, String summary, List<IssueDTO> issues) {
}
