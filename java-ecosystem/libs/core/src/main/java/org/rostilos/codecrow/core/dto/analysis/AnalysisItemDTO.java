package org.rostilos.codecrow.core.dto.analysis;

import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;

import java.time.OffsetDateTime;

public record AnalysisItemDTO(
    String id,
    String branch,
    String pullRequestId,
    String triggeredBy,
    OffsetDateTime triggeredAt,
    OffsetDateTime completedAt,
    String status, // pending|running|completed|failed
    String aiProvider,
    int issuesFound,
    int criticalIssuesFound,
    String duration
) {
    public static AnalysisItemDTO fromEntity(CodeAnalysis analysisItem) {
        return new AnalysisItemDTO(
                String.valueOf(analysisItem.getId()),
                analysisItem.getBranchName(),
                analysisItem.getPrNumber() == null ? null : String.valueOf(analysisItem.getPrNumber()),
                null,
                analysisItem.getCreatedAt(),
                analysisItem.getUpdatedAt(),
                analysisItem.getStatus() == null ? null : analysisItem.getStatus().name().toLowerCase(),
                null,
                analysisItem.getTotalIssues(),
                analysisItem.getHighSeverityCount(),
                analysisItem.getCreatedAt() != null && analysisItem.getUpdatedAt() != null
                        ? java.time.Duration.between(analysisItem.getCreatedAt(), analysisItem.getUpdatedAt()).getSeconds() + "s"
                        : null
        );
    }

}

