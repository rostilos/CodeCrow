package org.rostilos.codecrow.astparser.api;

import org.rostilos.codecrow.astparser.model.ParsedTree;
import org.rostilos.codecrow.astparser.model.SupportedLanguage;

/**
 * Parses source code into a concrete syntax tree.
 * <p>
 * Implementations must be thread-safe. The returned {@link ParsedTree}
 * holds native resources and must be closed by the caller.
 */
public interface AstParser {

    /**
     * Parse source code into an AST.
     *
     * @param sourceText the full file content
     * @param language   the programming language
     * @return a parsed tree (caller must close)
     * @throws AstParseException if parsing fails fatally (NOT for recoverable syntax errors —
     *                           tree-sitter is error-tolerant and will always produce a tree)
     */
    ParsedTree parse(String sourceText, SupportedLanguage language);

    /**
     * Check if the parser can handle the given language.
     * Some languages might not have grammars loaded at runtime.
     */
    boolean supports(SupportedLanguage language);
}
