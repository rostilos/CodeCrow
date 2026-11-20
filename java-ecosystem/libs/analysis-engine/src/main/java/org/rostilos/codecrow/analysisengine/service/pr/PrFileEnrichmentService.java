package org.rostilos.codecrow.analysisengine.service.pr;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.*;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.*;
import org.rostilos.codecrow.analysisengine.util.TextFileEligibility;
import org.rostilos.codecrow.vcsclient.RepositorySource;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.util.*;
import java.util.concurrent.TimeUnit;
import java.util.stream.Collectors;

/**
 * Service responsible for enriching PR analysis requests with full file
 * contents
 * and pre-computed dependency relationships.
 * 
 * This service:
 * 1. Fetches full file contents for changed files (with size limits)
 * 2. Calls RAG pipeline's /parse endpoint to extract AST metadata
 * 3. Builds a relationship graph from the parsed metadata
 * 4. Returns enriched data for intelligent batching in Python
 */
@Service
public class PrFileEnrichmentService {
    private static final Logger log = LoggerFactory.getLogger(PrFileEnrichmentService.class);

    private static final MediaType JSON_MEDIA_TYPE = MediaType.get("application/json; charset=utf-8");

    @Value("${pr.enrichment.enabled:true}")
    private boolean enrichmentEnabled;

    @Value("${pr.enrichment.max-file-size-bytes:5242880}") // 5MB default
    private long maxFileSizeBytes;

    @Value("${pr.enrichment.max-total-size-bytes:20971520}") // 20MB default
    private long maxTotalSizeBytes;

    @Value("${pr.enrichment.rag-pipeline-url:${codecrow.rag.api.url:http://codecrow-rag-pipeline:8001}}")
    private String ragPipelineUrl;

    @Value("${pr.enrichment.rag-api-secret:${codecrow.rag.api.secret:}}")
    private String ragApiSecret;

    @Value("${pr.enrichment.request-timeout-seconds:60}")
    private int requestTimeoutSeconds;

    private final ObjectMapper objectMapper;
    private final OkHttpClient httpClient;

    public PrFileEnrichmentService() {
        this.objectMapper = new ObjectMapper();
        this.httpClient = new OkHttpClient.Builder()
                .connectTimeout(30, TimeUnit.SECONDS)
                .readTimeout(60, TimeUnit.SECONDS)
                .writeTimeout(30, TimeUnit.SECONDS)
                .build();
    }

    /**
     * Check if PR enrichment is enabled.
     */
    public boolean isEnrichmentEnabled() {
        return enrichmentEnabled;
    }

    public PrEnrichmentDataDto enrichPrFiles(RepositorySource source, List<String> changedFiles) {
        if (!enrichmentEnabled) return PrEnrichmentDataDto.empty();
        return enrichContents(fetchFileContentsOnly(source, changedFiles), changedFiles);
    }

    /** Parse acquired content after the result-cache decision, without reacquiring source. */
    public PrEnrichmentDataDto enrichContents(PrEnrichmentDataDto contents, List<String> changedFiles) {
        if (!enrichmentEnabled || contents == null || !contents.hasData()) return contents;
        long started = System.currentTimeMillis();
        try {
            var metadata = parseFilesForMetadata(contents.fileContents());
            var relationships = buildRelationshipGraph(metadata, changedFiles != null ? changedFiles : List.of());
            var stats = contents.stats();
            return new PrEnrichmentDataDto(contents.fileContents(), metadata, relationships,
                    stats == null ? null : new PrEnrichmentDataDto.EnrichmentStats(
                            stats.totalFilesRequested(), stats.filesEnriched(), stats.filesSkipped(),
                            relationships.size(), stats.totalContentSizeBytes(),
                            stats.processingTimeMs() + System.currentTimeMillis() - started, stats.skipReasons()));
        } catch (Exception failure) {
            log.warn("Structured enrichment unavailable; retaining acquired source: {}", failure.getMessage());
            return contents;
        }
    }

    public PrEnrichmentDataDto fetchFileContentsOnly(
            RepositorySource source,
            List<String> changedFiles) {
        if (changedFiles == null || changedFiles.isEmpty()) {
            return PrEnrichmentDataDto.empty();
        }

        long startTime = System.currentTimeMillis();
        Map<String, Integer> skipReasons = new HashMap<>();

        try {
            List<String> supportedFiles = filterSupportedFiles(changedFiles, skipReasons);
            if (supportedFiles.isEmpty()) {
                return createEmptyResultWithStats(changedFiles.size(), 0, skipReasons, startTime);
            }

            Map<String, String> fileContents = source.readFiles(
                    supportedFiles,
                    (int) Math.min(maxFileSizeBytes, Integer.MAX_VALUE), maxTotalSizeBytes);

            List<FileContentDto> contentDtos = buildFileContentDtos(
                    supportedFiles, fileContents, skipReasons);

            long totalSize = contentDtos.stream()
                    .filter(f -> !f.skipped())
                    .mapToLong(FileContentDto::sizeBytes)
                    .sum();
            if (totalSize > maxTotalSizeBytes) {
                contentDtos = truncateToSizeLimit(contentDtos, maxTotalSizeBytes, skipReasons);
            }

            long processingTime = System.currentTimeMillis() - startTime;
            int filesEnriched = (int) contentDtos.stream().filter(f -> !f.skipped()).count();

            PrEnrichmentDataDto.EnrichmentStats stats = new PrEnrichmentDataDto.EnrichmentStats(
                    changedFiles.size(), filesEnriched,
                    changedFiles.size() - filesEnriched,
                    0, totalSize, processingTime, skipReasons);

            log.info("Local source acquisition: read {}/{} files in {}ms",
                    filesEnriched, changedFiles.size(), processingTime);

            return new PrEnrichmentDataDto(contentDtos, List.of(), List.of(), stats);

        } catch (Exception e) {
            log.warn("File-content-only fallback failed: {}", e.getMessage());
            throw new IllegalStateException(
                    "File-content acquisition failed before producing per-file outcomes", e);
        }
    }

    /**
     * Filter out known binary / non-text files. Everything not blacklisted is
     * allowed through for enrichment and file snapshots.
     */
    private List<String> filterSupportedFiles(List<String> files, Map<String, Integer> skipReasons) {
        List<String> supported = new ArrayList<>();
        for (String file : files) {
            if (!TextFileEligibility.isTextCandidate(file)) {
                skipReasons.merge("binary_or_non_text", 1, Integer::sum);
            } else {
                supported.add(file);
            }
        }

        return supported;
    }

    /**
     * Build FileContentDto list from fetched contents.
     */
    private List<FileContentDto> buildFileContentDtos(
            List<String> requestedFiles,
            Map<String, String> fileContents,
            Map<String, Integer> skipReasons) {
        List<FileContentDto> result = new ArrayList<>();

        for (String path : requestedFiles) {
            String content = fileContents.get(path);

            if (content == null) {
                result.add(FileContentDto.skipped(path, "fetch_failed"));
                skipReasons.merge("fetch_failed", 1, Integer::sum);
            } else {
                result.add(FileContentDto.of(path, content));
            }
        }

        return result;
    }

    /**
     * Truncate file list to stay within total size limit.
     * Prioritizes smaller files to maximize coverage.
     */
    private List<FileContentDto> truncateToSizeLimit(
            List<FileContentDto> contents,
            long maxTotalSize,
            Map<String, Integer> skipReasons) {
        // Sort by size (smallest first) to include more files
        List<FileContentDto> sorted = contents.stream()
                .filter(f -> !f.skipped())
                .sorted(Comparator.comparingLong(FileContentDto::sizeBytes))
                .collect(Collectors.toCollection(ArrayList::new));

        List<FileContentDto> result = new ArrayList<>();
        long currentSize = 0;

        for (FileContentDto file : sorted) {
            if (currentSize + file.sizeBytes() <= maxTotalSize) {
                result.add(file);
                currentSize += file.sizeBytes();
            } else {
                result.add(FileContentDto.skipped(file.path(), "total_size_limit_exceeded"));
                skipReasons.merge("total_size_limit", 1, Integer::sum);
            }
        }

        // Add already-skipped files
        contents.stream()
                .filter(FileContentDto::skipped)
                .forEach(result::add);

        return result;
    }

    /**
     * Call RAG pipeline's /parse/batch endpoint to extract AST metadata.
     */
    private List<ParsedFileMetadataDto> parseFilesForMetadata(List<FileContentDto> contents) {
        List<FileContentDto> filesToParse = contents.stream()
                .filter(f -> !f.skipped())
                .toList();

        if (filesToParse.isEmpty()) {
            return Collections.emptyList();
        }

        try {
            // Build batch request
            List<Map<String, String>> files = filesToParse.stream()
                    .map(f -> Map.of("path", f.path(), "content", f.content()))
                    .toList();

            Map<String, Object> requestBody = Map.of("files", files);
            String jsonBody = objectMapper.writeValueAsString(requestBody);

            Request.Builder requestBuilder = new Request.Builder()
                    .url(ragPipelineUrl + "/parse/batch")
                    .post(RequestBody.create(jsonBody, JSON_MEDIA_TYPE));
            if (ragApiSecret != null && !ragApiSecret.isBlank()) {
                requestBuilder.header("x-service-secret", ragApiSecret);
            }
            Request request = requestBuilder.build();

            try (Response response = httpClient.newCall(request).execute()) {
                if (!response.isSuccessful()) {
                    log.warn("RAG /parse/batch failed with status {}: {}",
                            response.code(), response.message());
                    return createFallbackMetadata(filesToParse);
                }

                ResponseBody responseBody = response.body();
                if (responseBody == null) {
                    return createFallbackMetadata(filesToParse);
                }

                ParseBatchResponse batchResponse = objectMapper.readValue(
                        responseBody.string(),
                        ParseBatchResponse.class);

                return batchResponse.results != null
                        ? batchResponse.results
                        : createFallbackMetadata(filesToParse);
            }

        } catch (IOException e) {
            log.warn("Failed to call RAG /parse/batch: {}", e.getMessage());
            return createFallbackMetadata(filesToParse);
        }
    }

    /**
     * Create minimal metadata when RAG parsing fails.
     */
    private List<ParsedFileMetadataDto> createFallbackMetadata(List<FileContentDto> files) {
        return files.stream()
                .map(f -> ParsedFileMetadataDto.error(f.path(), "parse_failed"))
                .toList();
    }

    /**
     * Build relationship graph from parsed metadata.
     * Matches imports/extends/calls against changed files.
     */
    private List<FileRelationshipDto> buildRelationshipGraph(
            List<ParsedFileMetadataDto> metadata,
            List<String> changedFiles) {
        List<FileRelationshipDto> relationships = new ArrayList<>();

        // Preserve every candidate for a name. A single-value map silently
        // turned duplicate basenames into arbitrary relationships.
        Map<String, Set<String>> nameToPaths = buildNameToPathMap(changedFiles);

        for (ParsedFileMetadataDto file : metadata) {
            if (file.error() != null)
                continue;

            // Process imports
            if (file.imports() != null) {
                for (String importStmt : file.imports()) {
                    String targetPath = findMatchingFile(importStmt, nameToPaths, changedFiles);
                    if (targetPath != null && !targetPath.equals(file.path())) {
                        relationships.add(FileRelationshipDto.imports(
                                file.path(), targetPath, importStmt));
                    }
                }
            }

            // Process extends
            if (file.extendsClasses() != null) {
                for (String className : file.extendsClasses()) {
                    String targetPath = findMatchingFile(className, nameToPaths, changedFiles);
                    if (targetPath != null && !targetPath.equals(file.path())) {
                        relationships.add(FileRelationshipDto.extendsClass(
                                file.path(), targetPath, className));
                    }
                }
            }

            // Process implements
            if (file.implementsInterfaces() != null) {
                for (String interfaceName : file.implementsInterfaces()) {
                    String targetPath = findMatchingFile(interfaceName, nameToPaths, changedFiles);
                    if (targetPath != null && !targetPath.equals(file.path())) {
                        relationships.add(FileRelationshipDto.implementsInterface(
                                file.path(), targetPath, interfaceName));
                    }
                }
            }

            // Process calls
            if (file.calls() != null) {
                for (String call : file.calls()) {
                    String targetPath = findMatchingFile(call, nameToPaths, changedFiles);
                    if (targetPath != null && !targetPath.equals(file.path())) {
                        relationships.add(FileRelationshipDto.calls(
                                file.path(), targetPath, call));
                    }
                }
            }
        }

        // Deduplicate relationships
        return relationships.stream()
                .distinct()
                .toList();
    }

    /**
     * Build a map of class/module names to file paths for matching.
     * Language-agnostic: maps both the bare filename (without extension)
     * and the dot-separated directory path for ALL file types.
     */
    private Map<String, Set<String>> buildNameToPathMap(List<String> filePaths) {
        Map<String, Set<String>> map = new HashMap<>();

        for (String path : filePaths) {
            String normalizedPath = normalizePath(path);
            String pathWithoutExt = stripFileExtension(normalizedPath);
            String nameWithoutExt = baseName(pathWithoutExt);

            addCandidate(map, normalizedPath, path);
            addCandidate(map, pathWithoutExt, path);
            addCandidate(map, pathWithoutExt.replace('/', '.'), path);

            // Bare names are only usable when the candidate set is unique.
            addCandidate(map, nameWithoutExt, path);
            addCandidate(map, nameWithoutExt.toLowerCase(Locale.ROOT), path);
        }

        return map;
    }

    /**
     * Find a matching file path for an import/extends statement.
     * Language-agnostic: matches by name, dot-path, or base filename.
     */
    private String findMatchingFile(
            String reference,
            Map<String, Set<String>> nameToPaths,
            List<String> changedFiles) {
        if (reference == null || reference.isBlank())
            return null;

        String trimmedReference = reference.trim();
        String directMatch = uniqueCandidate(nameToPaths.get(trimmedReference));
        if (directMatch != null) {
            return directMatch;
        }

        String normalizedReference = normalizeReference(trimmedReference);
        directMatch = uniqueCandidate(nameToPaths.get(normalizedReference));
        if (directMatch != null) {
            return directMatch;
        }

        // A qualified reference may match a path suffix, but only on a full
        // path-segment boundary and only when exactly one changed file matches.
        // This supports source-root prefixes without accepting substrings.
        if (isQualifiedReference(trimmedReference)) {
            Set<String> qualifiedMatches = new LinkedHashSet<>();
            for (String path : changedFiles) {
                String candidate = stripFileExtension(normalizePath(path));
                if (candidate.equals(normalizedReference)
                        || candidate.endsWith("/" + normalizedReference)) {
                    qualifiedMatches.add(path);
                }
            }
            // Qualification is evidence, not decoration. If it does not
            // identify exactly one changed path, do not discard it and guess
            // from a coincidentally unique basename.
            return uniqueCandidate(qualifiedMatches);
        }

        String simpleName = baseName(normalizedReference);
        String basenameMatch = uniqueCandidate(nameToPaths.get(simpleName));
        if (basenameMatch != null) {
            return basenameMatch;
        }

        return uniqueCandidate(nameToPaths.get(simpleName.toLowerCase(Locale.ROOT)));
    }

    private static void addCandidate(
            Map<String, Set<String>> candidates,
            String key,
            String path) {
        if (key != null && !key.isBlank()) {
            candidates.computeIfAbsent(key, ignored -> new LinkedHashSet<>()).add(path);
        }
    }

    private static String uniqueCandidate(Set<String> candidates) {
        return candidates != null && candidates.size() == 1
                ? candidates.iterator().next()
                : null;
    }

    private static boolean isQualifiedReference(String reference) {
        return reference.contains(".")
                || reference.contains("/")
                || reference.contains("\\")
                || reference.contains("::");
    }

    private static String normalizeReference(String reference) {
        String normalized = normalizePath(reference.replace("::", "/"));
        while (normalized.startsWith("./")) {
            normalized = normalized.substring(2);
        }
        return normalized.replace('.', '/');
    }

    private static String normalizePath(String value) {
        return value.replace('\\', '/');
    }

    private static String stripFileExtension(String path) {
        int lastSlash = path.lastIndexOf('/');
        int lastDot = path.lastIndexOf('.');
        return lastDot > lastSlash ? path.substring(0, lastDot) : path;
    }

    private static String baseName(String path) {
        int lastSlash = path.lastIndexOf('/');
        return lastSlash >= 0 ? path.substring(lastSlash + 1) : path;
    }

    private PrEnrichmentDataDto createEmptyResultWithStats(
            int totalFiles,
            int enriched,
            Map<String, Integer> skipReasons,
            long startTime) {
        long processingTime = System.currentTimeMillis() - startTime;
        return new PrEnrichmentDataDto(
                Collections.emptyList(),
                Collections.emptyList(),
                Collections.emptyList(),
                new PrEnrichmentDataDto.EnrichmentStats(
                        totalFiles, enriched, totalFiles - enriched,
                        0, 0, processingTime, skipReasons));
    }

    /**
     * Response DTO for /parse/batch endpoint.
     */
    @JsonIgnoreProperties(ignoreUnknown = true)
    private static class ParseBatchResponse {
        @JsonProperty("results")
        public List<ParsedFileMetadataDto> results;

        @JsonProperty("total_files")
        public int totalFiles;

        @JsonProperty("successful")
        public int successful;

        @JsonProperty("failed")
        public int failed;
    }
}
