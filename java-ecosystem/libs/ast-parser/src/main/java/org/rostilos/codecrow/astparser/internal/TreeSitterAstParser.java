package org.rostilos.codecrow.astparser.internal;

import org.rostilos.codecrow.astparser.api.AstParseException;
import org.rostilos.codecrow.astparser.api.AstParser;
import org.rostilos.codecrow.astparser.model.ParsedTree;
import org.rostilos.codecrow.astparser.model.SupportedLanguage;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.treesitter.TSParser;
import org.treesitter.TSTree;

/**
 * Tree-sitter backed implementation of {@link AstParser}.
 * <p>
 * Uses {@link ParserPool} for thread safety — each parse borrows a parser,
 * uses it, and returns it to the pool.
 * <p>
 * Tree-sitter is error-tolerant: even malformed code produces a syntax tree
 * (with ERROR nodes). This is intentional — code review targets real-world
 * code that may have syntax errors.
 */
public final class TreeSitterAstParser implements AstParser {

    private static final Logger log = LoggerFactory.getLogger(TreeSitterAstParser.class);

    private final ParserPool parserPool;

    public TreeSitterAstParser(ParserPool parserPool) {
        if (parserPool == null) throw new IllegalArgumentException("parserPool must not be null");
        this.parserPool = parserPool;
    }

    @Override
    public ParsedTree parse(String sourceText, SupportedLanguage language) {
        if (sourceText == null) throw new IllegalArgumentException("sourceText must not be null");
        if (language == null) throw new IllegalArgumentException("language must not be null");

        TSParser parser = parserPool.borrow(language);
        try {
            TSTree tree = parser.parseString(null, sourceText);
            if (tree == null) {
                throw new AstParseException(
                        "tree-sitter returned null tree for " + language + " (source length: " + sourceText.length() + ")");
            }

            ParsedTree result = new ParsedTree(tree, sourceText, language);
            if (result.hasErrors()) {
                log.debug("Parse produced error nodes for {} ({} chars). Tree is still usable.",
                        language, sourceText.length());
            }
            return result;
        } catch (AstParseException e) {
            throw e;
        } catch (Exception e) {
            throw new AstParseException("Failed to parse " + language + " source", e);
        } finally {
            parserPool.release(language, parser);
        }
    }

    @Override
    public boolean supports(SupportedLanguage language) {
        if (language == null) return false;
        try {
            parserPool.getGrammar(language);
            return true;
        } catch (Exception e) {
            return false;
        }
    }
}
