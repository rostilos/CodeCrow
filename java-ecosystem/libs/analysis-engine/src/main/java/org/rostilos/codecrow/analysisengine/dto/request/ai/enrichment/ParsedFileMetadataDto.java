package org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;

/**
 * DTO representing parsed AST metadata for a single file.
 * Mirrors the response from RAG pipeline's /parse endpoint.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record ParsedFileMetadataDto(
        @JsonProperty("path") String path,
        @JsonProperty("language") String language,
        @JsonProperty("imports") List<String> imports,
        @JsonProperty("extends") List<String> extendsClasses,
        @JsonProperty("implements") List<String> implementsInterfaces,
        @JsonProperty("semantic_names") List<String> semanticNames,
        @JsonProperty("parent_class") String parentClass,
        @JsonProperty("namespace") String namespace,
        @JsonProperty("calls") List<String> calls,
        @JsonProperty("error") String error
) {
    /**
     * Create a metadata result with only imports and extends (minimal parsing).
     */
    public static ParsedFileMetadataDto minimal(String path, List<String> imports, List<String> extendsClasses) {
        return new ParsedFileMetadataDto(
                path,
                null,
                imports,
                extendsClasses,
                List.of(),
                List.of(),
                null,
                null,
                List.of(),
                null
        );
    }

    /**
     * Create an error result for a file that couldn't be parsed.
     */
    public static ParsedFileMetadataDto error(String path, String errorMessage) {
        return new ParsedFileMetadataDto(
                path,
                null,
                List.of(),
                List.of(),
                List.of(),
                List.of(),
                null,
                null,
                List.of(),
                errorMessage
        );
    }

    /**
     * Check if this metadata has any relationships to extract.
     */
    public boolean hasRelationships() {
        return (imports != null && !imports.isEmpty()) ||
               (extendsClasses != null && !extendsClasses.isEmpty()) ||
               (implementsInterfaces != null && !implementsInterfaces.isEmpty()) ||
               (calls != null && !calls.isEmpty());
    }
}
