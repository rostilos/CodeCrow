package org.rostilos.codecrow.astparser.model;

import org.treesitter.TSNode;
import org.treesitter.TSTree;

/**
 * Wrapper around a tree-sitter parse result.
 * <p>
 * Holds the {@link TSTree} alongside the source text and language metadata
 * so downstream consumers (scope resolver, symbol extractor) have everything
 * they need without passing raw arguments everywhere.
 * <p>
 * Implements {@link AutoCloseable} because the underlying {@link TSTree}
 * holds native memory allocated via JNI.
 *
 * <h3>Thread safety</h3>
 * {@link TSTree} is immutable once parsed and safe to read from multiple threads.
 * However, the {@link #close()} method must be called exactly once — typically
 * via try-with-resources in the calling code.
 */
public final class ParsedTree implements AutoCloseable {

    private final TSTree tree;
    private final String sourceText;
    private final SupportedLanguage language;

    public ParsedTree(TSTree tree, String sourceText, SupportedLanguage language) {
        if (tree == null) throw new IllegalArgumentException("tree must not be null");
        if (sourceText == null) throw new IllegalArgumentException("sourceText must not be null");
        if (language == null) throw new IllegalArgumentException("language must not be null");
        this.tree = tree;
        this.sourceText = sourceText;
        this.language = language;
    }

    /** The raw tree-sitter tree. */
    public TSTree getTree() {
        return tree;
    }

    /** The root node of the syntax tree. */
    public TSNode getRootNode() {
        return tree.getRootNode();
    }

    /** Full source text that was parsed. */
    public String getSourceText() {
        return sourceText;
    }

    /** The language this tree was parsed as. */
    public SupportedLanguage getLanguage() {
        return language;
    }

    /**
     * Check if the parse produced any ERROR or MISSING nodes,
     * which indicates the file has syntax errors.
     */
    public boolean hasErrors() {
        return getRootNode().hasError();
    }

    @Override
    public void close() {
        tree.close();
    }
}
