package org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/** One exact source symbol returned by the RAG parser. */
@JsonIgnoreProperties(ignoreUnknown = true)
public record ParsedSymbolDto(
        @JsonProperty("symbol_id") String symbolId,
        @JsonProperty("path") String path,
        @JsonProperty("name") String name,
        @JsonProperty("qualified_name") String qualifiedName,
        @JsonProperty("kind") String kind,
        @JsonProperty("start_line") int startLine,
        @JsonProperty("end_line") int endLine,
        @JsonProperty("parent_symbol") String parentSymbol,
        @JsonProperty("signature") String signature,
        @JsonProperty("parameters") List<String> parameters,
        @JsonProperty("return_type") String returnType,
        @JsonProperty("modifiers") List<String> modifiers,
        @JsonProperty("decorators") List<String> decorators,
        @JsonProperty("extraction_method") String extractionMethod
) {
    public ParsedSymbolDto {
        parameters = immutableListCopy(parameters);
        modifiers = immutableListCopy(modifiers);
        decorators = immutableListCopy(decorators);
    }

    private static <E> List<E> immutableListCopy(List<? extends E> source) {
        return source == null
                ? List.of()
                : Collections.unmodifiableList(new ArrayList<>(source));
    }
}
