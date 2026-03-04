package org.rostilos.codecrow.astparser.model;

import java.util.List;

/**
 * Symbol information extracted from a parsed file's AST.
 * <p>
 * Used by the enrichment pipeline to replace the Python RAG {@code /parse} endpoint
 * with a pure-Java extraction. Each field mirrors what the LLM/RAG pipeline
 * needs for contextual understanding:
 * <ul>
 *   <li>{@code imports} — dependency graph, coupling detection</li>
 *   <li>{@code classes} — structural overview, God-class detection</li>
 *   <li>{@code functions} — function inventory, complexity analysis</li>
 *   <li>{@code calls} — call graph, dead code, coupling</li>
 *   <li>{@code namespace} — package/module resolution</li>
 *   <li>{@code parentClass} — inheritance chain</li>
 * </ul>
 *
 * @param imports      list of import statements / require calls
 * @param classes      list of class/struct/trait/interface names
 * @param functions    list of function/method names
 * @param calls        list of external function/method calls
 * @param namespace    resolved namespace/package (empty string if none)
 * @param parentClass  extends/inherits target (empty string if none)
 * @param scopes       all resolved scopes ordered by start line
 */
public record SymbolInfo(
        List<String> imports,
        List<String> classes,
        List<String> functions,
        List<String> calls,
        String namespace,
        String parentClass,
        List<ScopeInfo> scopes
) {
    /** Empty symbol info for files that failed to parse or have no symbols. */
    public static SymbolInfo empty() {
        return new SymbolInfo(
                List.of(), List.of(), List.of(), List.of(),
                "", "", List.of()
        );
    }
}
