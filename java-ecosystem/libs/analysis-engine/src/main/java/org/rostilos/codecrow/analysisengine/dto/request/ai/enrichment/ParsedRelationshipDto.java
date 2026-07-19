package org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

/** One typed symbol edge, including exact-batch repository resolution. */
@JsonIgnoreProperties(ignoreUnknown = true)
public record ParsedRelationshipDto(
        @JsonProperty("relationship_id") String relationshipId,
        @JsonProperty("source_symbol_id") String sourceSymbolId,
        @JsonProperty("source_name") String sourceName,
        @JsonProperty("target_name") String targetName,
        @JsonProperty("relationship_type") String relationshipType,
        @JsonProperty("source_line") int sourceLine,
        @JsonProperty("target_symbol_id") String targetSymbolId,
        @JsonProperty("target_path") String targetPath,
        @JsonProperty("resolution") String resolution,
        @JsonProperty("confidence") double confidence
) {
    public boolean isResolved() {
        return "resolved".equals(resolution)
                && targetSymbolId != null
                && targetPath != null
                && !targetPath.isBlank();
    }
}
