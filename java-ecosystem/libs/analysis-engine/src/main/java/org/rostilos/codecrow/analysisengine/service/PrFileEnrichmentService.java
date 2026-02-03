package org.rostilos.codecrow.analysisengine.service;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.*;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.*;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.util.*;
import java.util.concurrent.TimeUnit;
import java.util.stream.Collectors;

/**
 * Service responsible for enriching PR analysis requests with full file contents
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
    
    @Value("${pr.enrichment.max-file-size-bytes:102400}")  // 100KB default
    private long maxFileSizeBytes;
    
    @Value("${pr.enrichment.max-total-size-bytes:10485760}")  // 10MB default
    private long maxTotalSizeBytes;
    
    @Value("${pr.enrichment.rag-pipeline-url:http://localhost:8006}")
    private String ragPipelineUrl;
    
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
    
    /**
     * Enrich PR analysis with full file contents and dependency graph.
     *
     * @param vcsClient     VCS client for fetching file contents
     * @param workspace     VCS workspace/owner
     * @param repoSlug      Repository slug
     * @param branch        Branch name or commit SHA for file content
     * @param changedFiles  List of changed file paths
     * @return Enrichment data with file contents and relationships
     */
    public PrEnrichmentDataDto enrichPrFiles(
            VcsClient vcsClient,
            String workspace,
            String repoSlug,
            String branch,
            List<String> changedFiles
    ) {
        if (!enrichmentEnabled) {
            log.debug("PR enrichment is disabled");
            return PrEnrichmentDataDto.empty();
        }
        
        if (changedFiles == null || changedFiles.isEmpty()) {
            log.debug("No changed files to enrich");
            return PrEnrichmentDataDto.empty();
        }
        
        long startTime = System.currentTimeMillis();
        Map<String, Integer> skipReasons = new HashMap<>();
        
        try {
            // Step 1: Filter to supported file types
            List<String> supportedFiles = filterSupportedFiles(changedFiles, skipReasons);
            if (supportedFiles.isEmpty()) {
                log.info("No supported files to enrich after filtering");
                return createEmptyResultWithStats(changedFiles.size(), 0, skipReasons, startTime);
            }
            
            // Step 2: Fetch file contents in batch
            log.info("Fetching {} file contents for enrichment (branch: {})", supportedFiles.size(), branch);
            Map<String, String> fileContents = vcsClient.getFileContents(
                    workspace, repoSlug, supportedFiles, branch, (int) Math.min(maxFileSizeBytes, Integer.MAX_VALUE)
            );
            
            // Step 3: Build FileContentDto list
            List<FileContentDto> contentDtos = buildFileContentDtos(
                    supportedFiles, fileContents, skipReasons
            );
            
            // Check total size limit
            long totalSize = contentDtos.stream()
                    .filter(f -> !f.skipped())
                    .mapToLong(FileContentDto::sizeBytes)
                    .sum();
            
            if (totalSize > maxTotalSizeBytes) {
                log.warn("Total file content size {} exceeds limit {} - truncating", 
                        totalSize, maxTotalSizeBytes);
                contentDtos = truncateToSizeLimit(contentDtos, maxTotalSizeBytes, skipReasons);
            }
            
            // Step 4: Parse files to extract AST metadata
            List<ParsedFileMetadataDto> metadata = parseFilesForMetadata(contentDtos);
            
            // Step 5: Build relationship graph from metadata
            List<FileRelationshipDto> relationships = buildRelationshipGraph(
                    metadata, changedFiles
            );
            
            long processingTime = System.currentTimeMillis() - startTime;
            
            // Build stats
            int filesEnriched = (int) contentDtos.stream().filter(f -> !f.skipped()).count();
            int filesSkipped = changedFiles.size() - filesEnriched;
            
            PrEnrichmentDataDto.EnrichmentStats stats = new PrEnrichmentDataDto.EnrichmentStats(
                    changedFiles.size(),
                    filesEnriched,
                    filesSkipped,
                    relationships.size(),
                    totalSize,
                    processingTime,
                    skipReasons
            );
            
            log.info("PR enrichment completed: {} files enriched, {} skipped, {} relationships in {}ms",
                    filesEnriched, filesSkipped, relationships.size(), processingTime);
            
            return new PrEnrichmentDataDto(contentDtos, metadata, relationships, stats);
            
        } catch (Exception e) {
            log.error("Failed to enrich PR files: {}", e.getMessage(), e);
            return createEmptyResultWithStats(
                    changedFiles.size(), 0, 
                    Map.of("error", changedFiles.size()),
                    startTime
            );
        }
    }
    
    /**
     * Filter files to only those with supported extensions for parsing.
     */
    private List<String> filterSupportedFiles(List<String> files, Map<String, Integer> skipReasons) {
        Set<String> supportedExtensions = Set.of(
                ".java", ".py", ".js", ".ts", ".jsx", ".tsx",
                ".go", ".rs", ".rb", ".php", ".cs", ".cpp", ".c", ".h",
                ".kt", ".scala", ".swift", ".m", ".mm"
        );
        
        List<String> supported = new ArrayList<>();
        for (String file : files) {
            String lower = file.toLowerCase();
            boolean isSupported = supportedExtensions.stream()
                    .anyMatch(lower::endsWith);
            
            if (isSupported) {
                supported.add(file);
            } else {
                skipReasons.merge("unsupported_extension", 1, Integer::sum);
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
            Map<String, Integer> skipReasons
    ) {
        List<FileContentDto> result = new ArrayList<>();
        
        for (String path : requestedFiles) {
            String content = fileContents.get(path);
            
            if (content == null) {
                result.add(FileContentDto.skipped(path, "fetch_failed"));
                skipReasons.merge("fetch_failed", 1, Integer::sum);
            } else if (content.isEmpty()) {
                result.add(FileContentDto.skipped(path, "empty_file"));
                skipReasons.merge("empty_file", 1, Integer::sum);
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
            Map<String, Integer> skipReasons
    ) {
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
            
            Request request = new Request.Builder()
                    .url(ragPipelineUrl + "/parse/batch")
                    .post(RequestBody.create(jsonBody, JSON_MEDIA_TYPE))
                    .build();
            
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
                        ParseBatchResponse.class
                );
                
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
            List<String> changedFiles
    ) {
        List<FileRelationshipDto> relationships = new ArrayList<>();
        
        // Build a map of possible file matches for quick lookup
        Map<String, String> nameToPath = buildNameToPathMap(changedFiles);
        
        for (ParsedFileMetadataDto file : metadata) {
            if (file.error() != null) continue;
            
            // Process imports
            if (file.imports() != null) {
                for (String importStmt : file.imports()) {
                    String targetPath = findMatchingFile(importStmt, nameToPath, changedFiles);
                    if (targetPath != null && !targetPath.equals(file.path())) {
                        relationships.add(FileRelationshipDto.imports(
                                file.path(), targetPath, importStmt));
                    }
                }
            }
            
            // Process extends
            if (file.extendsClasses() != null) {
                for (String className : file.extendsClasses()) {
                    String targetPath = findMatchingFile(className, nameToPath, changedFiles);
                    if (targetPath != null && !targetPath.equals(file.path())) {
                        relationships.add(FileRelationshipDto.extendsClass(
                                file.path(), targetPath, className));
                    }
                }
            }
            
            // Process implements
            if (file.implementsInterfaces() != null) {
                for (String interfaceName : file.implementsInterfaces()) {
                    String targetPath = findMatchingFile(interfaceName, nameToPath, changedFiles);
                    if (targetPath != null && !targetPath.equals(file.path())) {
                        relationships.add(FileRelationshipDto.implementsInterface(
                                file.path(), targetPath, interfaceName));
                    }
                }
            }
            
            // Process calls
            if (file.calls() != null) {
                for (String call : file.calls()) {
                    String targetPath = findMatchingFile(call, nameToPath, changedFiles);
                    if (targetPath != null && !targetPath.equals(file.path())) {
                        relationships.add(FileRelationshipDto.calls(
                                file.path(), targetPath, call));
                    }
                }
            }
        }
        
        // Add same-package relationships
        addSamePackageRelationships(changedFiles, relationships);
        
        // Deduplicate relationships
        return relationships.stream()
                .distinct()
                .toList();
    }
    
    /**
     * Build a map of class/module names to file paths for matching.
     */
    private Map<String, String> buildNameToPathMap(List<String> filePaths) {
        Map<String, String> map = new HashMap<>();
        
        for (String path : filePaths) {
            // Extract filename without extension
            String fileName = path.contains("/") 
                    ? path.substring(path.lastIndexOf('/') + 1) 
                    : path;
            String nameWithoutExt = fileName.contains(".")
                    ? fileName.substring(0, fileName.lastIndexOf('.'))
                    : fileName;
            
            map.put(nameWithoutExt, path);
            map.put(nameWithoutExt.toLowerCase(), path);
            
            // For Java-style paths, also map the package structure
            // e.g., com/example/MyClass.java -> MyClass
            if (path.endsWith(".java")) {
                String className = nameWithoutExt;
                map.put(className, path);
                
                // Also map full package path without extension
                String fullPath = path.replace('/', '.').replace(".java", "");
                map.put(fullPath, path);
            }
            
            // For Python, map module paths
            if (path.endsWith(".py")) {
                String modulePath = path.replace('/', '.').replace(".py", "");
                map.put(modulePath, path);
            }
        }
        
        return map;
    }
    
    /**
     * Find a matching file path for an import/extends statement.
     */
    private String findMatchingFile(
            String reference,
            Map<String, String> nameToPath,
            List<String> changedFiles
    ) {
        if (reference == null || reference.isEmpty()) return null;
        
        // Try direct match
        if (nameToPath.containsKey(reference)) {
            return nameToPath.get(reference);
        }
        
        // Extract last component (class name from qualified name)
        String simpleName = reference.contains(".")
                ? reference.substring(reference.lastIndexOf('.') + 1)
                : reference;
        
        if (nameToPath.containsKey(simpleName)) {
            return nameToPath.get(simpleName);
        }
        
        // Try case-insensitive
        String lowerName = simpleName.toLowerCase();
        if (nameToPath.containsKey(lowerName)) {
            return nameToPath.get(lowerName);
        }
        
        // Try partial path matching
        String normalizedRef = reference.replace('.', '/');
        for (String path : changedFiles) {
            if (path.contains(normalizedRef) || path.endsWith(simpleName + ".java") 
                    || path.endsWith(simpleName + ".py")
                    || path.endsWith(simpleName + ".ts")
                    || path.endsWith(simpleName + ".js")) {
                return path;
            }
        }
        
        return null;
    }
    
    /**
     * Add implicit relationships for files in the same package/directory.
     */
    private void addSamePackageRelationships(
            List<String> changedFiles,
            List<FileRelationshipDto> relationships
    ) {
        // Group files by directory
        Map<String, List<String>> filesByDir = changedFiles.stream()
                .collect(Collectors.groupingBy(path -> {
                    int lastSlash = path.lastIndexOf('/');
                    return lastSlash > 0 ? path.substring(0, lastSlash) : "";
                }));
        
        // Add relationships within each directory
        for (Map.Entry<String, List<String>> entry : filesByDir.entrySet()) {
            List<String> filesInDir = entry.getValue();
            String packageName = entry.getKey();
            
            if (filesInDir.size() > 1) {
                for (int i = 0; i < filesInDir.size(); i++) {
                    for (int j = i + 1; j < filesInDir.size(); j++) {
                        relationships.add(FileRelationshipDto.samePackage(
                                filesInDir.get(i), filesInDir.get(j), packageName));
                    }
                }
            }
        }
    }
    
    private PrEnrichmentDataDto createEmptyResultWithStats(
            int totalFiles,
            int enriched,
            Map<String, Integer> skipReasons,
            long startTime
    ) {
        long processingTime = System.currentTimeMillis() - startTime;
        return new PrEnrichmentDataDto(
                Collections.emptyList(),
                Collections.emptyList(),
                Collections.emptyList(),
                new PrEnrichmentDataDto.EnrichmentStats(
                        totalFiles, enriched, totalFiles - enriched,
                        0, 0, processingTime, skipReasons
                )
        );
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
