package org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment;

import com.fasterxml.jackson.annotation.JsonInclude;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiRequestPreviousIssueDTO;
import org.rostilos.codecrow.core.model.project.config.ReviewApproach;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

/**
 * Aggregate DTO containing all file enrichment data for a PR.
 * This is the result of the enrichment process and can be serialized for transfer.
 */
public record PrEnrichmentDataDto(
        List<FileContentDto> fileContents,
        List<ParsedFileMetadataDto> fileMetadata,
        List<FileRelationshipDto> relationships,
        EnrichmentStats stats,
        @JsonInclude(JsonInclude.Include.NON_NULL) ReviewContext reviewContext
) {
    public static final int LEGACY_REVIEW_CONTEXT_SCHEMA_VERSION = 1;
    public static final int CURRENT_REVIEW_CONTEXT_SCHEMA_VERSION = 2;

    public PrEnrichmentDataDto {
        fileContents = immutableListCopy(fileContents);
        fileMetadata = immutableListCopy(fileMetadata);
        relationships = immutableListCopy(relationships);
    }

    /** Keeps context-free enrichment byte-compatible with the original shape. */
    public PrEnrichmentDataDto(
            List<FileContentDto> fileContents,
            List<ParsedFileMetadataDto> fileMetadata,
            List<FileRelationshipDto> relationships,
            EnrichmentStats stats) {
        this(fileContents, fileMetadata, relationships, stats, null);
    }

    /** Useful prompt context frozen into the existing immutable enrichment artifact. */
    public record ReviewContext(
            int schemaVersion,
            String prTitle,
            String prDescription,
            String prAuthor,
            Map<String, String> taskContext,
            String taskHistoryContext,
            String projectRules,
            String sourceBranchName,
            String targetBranchName,
            @JsonInclude(JsonInclude.Include.NON_EMPTY)
            List<AiRequestPreviousIssueDTO> previousFindings,
            @JsonInclude(JsonInclude.Include.NON_NULL)
            ReviewApproach reviewApproach) {
        public ReviewContext {
            if (schemaVersion != LEGACY_REVIEW_CONTEXT_SCHEMA_VERSION
                    && schemaVersion != CURRENT_REVIEW_CONTEXT_SCHEMA_VERSION) {
                throw new IllegalArgumentException(
                        "reviewContext schemaVersion must be 1 or 2");
            }
            if (schemaVersion == CURRENT_REVIEW_CONTEXT_SCHEMA_VERSION
                    && reviewApproach == null) {
                throw new IllegalArgumentException(
                        "reviewContext reviewApproach is required for schemaVersion 2");
            }
            if (schemaVersion == LEGACY_REVIEW_CONTEXT_SCHEMA_VERSION
                    && reviewApproach != null) {
                throw new IllegalArgumentException(
                        "reviewContext schemaVersion 1 cannot bind reviewApproach");
            }
            if (taskContext != null && taskContext.entrySet().stream().anyMatch(
                    entry -> entry.getKey() == null || entry.getValue() == null)) {
                throw new IllegalArgumentException(
                        "reviewContext taskContext cannot contain null keys or values");
            }
            if (taskHistoryContext == null || projectRules == null) {
                throw new IllegalArgumentException(
                        "reviewContext history and projectRules are required");
            }
            if (sourceBranchName == null || sourceBranchName.isBlank()
                    || targetBranchName == null || targetBranchName.isBlank()) {
                throw new IllegalArgumentException(
                        "reviewContext source and target branches are required");
            }
            taskContext = taskContext == null
                    ? Map.of()
                    : Collections.unmodifiableMap(new TreeMap<>(taskContext));
            previousFindings = previousFindings == null
                    ? List.of()
                    : Collections.unmodifiableList(new ArrayList<>(previousFindings));
        }

        /**
         * Keeps existing callers and enrichment artifact bytes compatible when
         * no previous findings are bound to the review context.
         */
        public ReviewContext(
                int schemaVersion,
                String prTitle,
                String prDescription,
                String prAuthor,
                Map<String, String> taskContext,
                String taskHistoryContext,
                String projectRules,
                String sourceBranchName,
                String targetBranchName) {
            this(
                    schemaVersion,
                    prTitle,
                    prDescription,
                    prAuthor,
                    taskContext,
                    taskHistoryContext,
                    projectRules,
                    sourceBranchName,
                    targetBranchName,
                    List.of(),
                    null);
        }

        /** Preserves the schema-v1 constructor used by existing artifact readers. */
        public ReviewContext(
                int schemaVersion,
                String prTitle,
                String prDescription,
                String prAuthor,
                Map<String, String> taskContext,
                String taskHistoryContext,
                String projectRules,
                String sourceBranchName,
                String targetBranchName,
                List<AiRequestPreviousIssueDTO> previousFindings) {
            this(
                    schemaVersion,
                    prTitle,
                    prDescription,
                    prAuthor,
                    taskContext,
                    taskHistoryContext,
                    projectRules,
                    sourceBranchName,
                    targetBranchName,
                    previousFindings,
                    null);
        }
    }

    public PrEnrichmentDataDto withReviewContext(ReviewContext context) {
        return new PrEnrichmentDataDto(
                fileContents, fileMetadata, relationships, stats, context);
    }

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
        public EnrichmentStats {
            skipReasons = skipReasons == null
                    ? null
                    : Collections.unmodifiableMap(new TreeMap<>(skipReasons));
        }

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
                EnrichmentStats.empty(),
                null
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

    private static <E> List<E> immutableListCopy(List<? extends E> source) {
        return source == null
                ? null
                : Collections.unmodifiableList(new ArrayList<>(source));
    }
}
