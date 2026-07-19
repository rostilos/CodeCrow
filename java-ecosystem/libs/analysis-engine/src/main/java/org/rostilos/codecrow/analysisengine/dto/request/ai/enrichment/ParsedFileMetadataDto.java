package org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.ArrayList;
import java.util.Collections;
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
        @JsonProperty("content_digest") String contentDigest,
        @JsonProperty("parser_version") String parserVersion,
        @JsonProperty("ast_supported") Boolean astSupported,
        @JsonProperty("symbols") List<ParsedSymbolDto> symbols,
        @JsonProperty("relationships") List<ParsedRelationshipDto> relationships,
        @JsonProperty("degraded_reason") String degradedReason,
        @JsonProperty("error") String error
) {
    public ParsedFileMetadataDto {
        imports = immutableListCopy(imports);
        extendsClasses = immutableListCopy(extendsClasses);
        implementsInterfaces = immutableListCopy(implementsInterfaces);
        semanticNames = immutableListCopy(semanticNames);
        calls = immutableListCopy(calls);
        symbols = immutableListCopy(symbols);
        relationships = immutableListCopy(relationships);
    }

    /** Backwards-compatible constructor for legacy parser payload tests/callers. */
    public ParsedFileMetadataDto(
            String path,
            String language,
            List<String> imports,
            List<String> extendsClasses,
            List<String> implementsInterfaces,
            List<String> semanticNames,
            String parentClass,
            String namespace,
            List<String> calls,
            String error) {
        this(
                path,
                language,
                imports,
                extendsClasses,
                implementsInterfaces,
                semanticNames,
                parentClass,
                namespace,
                calls,
                null,
                null,
                null,
                List.of(),
                List.of(),
                null,
                error);
    }

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
                null,
                null,
                null,
                List.of(),
                List.of(),
                null,
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
                null,
                null,
                null,
                List.of(),
                List.of(),
                null,
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
               (calls != null && !calls.isEmpty()) ||
               (relationships != null && !relationships.isEmpty());
    }

    private static <E> List<E> immutableListCopy(List<? extends E> source) {
        return source == null
                ? null
                : Collections.unmodifiableList(new ArrayList<>(source));
    }
}
