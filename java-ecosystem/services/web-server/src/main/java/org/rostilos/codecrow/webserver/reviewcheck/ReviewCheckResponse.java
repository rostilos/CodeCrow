package org.rostilos.codecrow.webserver.reviewcheck;

import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.model.reviewcheck.ReviewCheckResult;
import java.util.List;

public record ReviewCheckResponse(Long analysisId, int version, List<Check> checks) {
    public record Check(String id, boolean complete, List<String> diagnostics, List<Finding> findings) {
        static Check from(ReviewCheckResult result) {
            return new Check(result.getCheckId(), result.isComplete(), result.getDiagnostics(), result.getFindings().stream()
                    .map(f -> new Finding(f.getId(), f.getSeverity(), f.getFilePath(), f.getLineNumber(), f.getTitle(),
                            f.getReason(), f.getSuggestedFixDescription(), f.getCodeSnippet(), f.isResolved(), f.getResolvedDescription())).toList());
        }
    }
    public record Finding(Long id, IssueSeverity severity, String file, Integer line, String title, String description,
                          String suggestedFix, String codeSnippet, boolean resolved, String resolution) {}
}
