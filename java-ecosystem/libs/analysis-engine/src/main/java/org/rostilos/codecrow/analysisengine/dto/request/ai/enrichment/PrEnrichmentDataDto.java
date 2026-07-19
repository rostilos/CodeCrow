package org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment;

import java.util.List;
import java.util.Map;

/**
 * Aggregate DTO containing all file enrichment data for a PR.
 * This is the result of the enrichment process and can be serialized for transfer.
 */
public record PrEnrichmentDataDto(
        List<FileContentDto> fileContents,
        List<ParsedFileMetadataDto> fileMetadata,
        List<FileRelationshipDto> relationships,
        EnrichmentStats stats
) {
    /**
     * Statistics about the enrichment process.
     */
    public record EnrichmentStats(
            int totalFilesRequested,
            int filesEnriched,
            int filesSkipped,
            int relationshipsFound,
            long totalContentSizeBytes,
            long processingTimeMs,
            Map<String, Integer> skipReasons
    ) {
        public static EnrichmentStats empty() {
            return new EnrichmentStats(0, 0, 0, 0, 0, 0, Map.of());
        }
    }

    /**
     * Create empty enrichment data (when enrichment is disabled or not applicable).
     */
    public static PrEnrichmentDataDto empty() {
        return new PrEnrichmentDataDto(
                List.of(),
                List.of(),
                List.of(),
                EnrichmentStats.empty()
        );
    }

    /**
     * Check if enrichment data is present.
     */
    public boolean hasData() {
        return (fileContents != null && !fileContents.isEmpty()) ||
               (relationships != null && !relationships.isEmpty());
    }

    /**
     * Get total size of all file contents in bytes.
     */
    public long getTotalContentSize() {
        if (fileContents == null) return 0;
        return fileContents.stream()
                .filter(f -> !f.skipped())
                .mapToLong(FileContentDto::sizeBytes)
                .sum();
    }
}
