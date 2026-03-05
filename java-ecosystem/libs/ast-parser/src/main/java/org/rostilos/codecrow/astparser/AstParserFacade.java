package org.rostilos.codecrow.astparser;

import org.rostilos.codecrow.astparser.api.*;
import org.rostilos.codecrow.astparser.internal.*;
import org.rostilos.codecrow.astparser.model.*;
import org.rostilos.codecrow.core.util.tracking.LineHashSequence;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.List;
import java.util.Optional;

/**
 * Primary entry point for the AST parser module.
 * <p>
 * Composes all internal components ({@link AstParser}, {@link ScopeResolver},
 * {@link SymbolExtractor}, {@link ScopeAwareHasher}) behind a single facade
 * that the rest of the platform depends on.
 * <p>
 * Typical usage:
 * <pre>{@code
 * AstParserFacade facade = AstParserFacade.createDefault();
 *
 * // For scope resolution during issue tracking:
 * Optional<ScopeInfo> scope = facade.resolveInnermostScope("path/to/File.java", source, 42);
 * scope.ifPresent(s -> {
 *     String hash = facade.computeScopeHash(source, s);
 * });
 *
 * // For enrichment during PR analysis:
 * SymbolInfo symbols = facade.extractSymbols("path/to/File.py", source);
 * }</pre>
 *
 * <h3>Thread safety</h3>
 * Fully thread-safe. All internal components use pooling or are stateless.
 *
 * <h3>Lifecycle</h3>
 * Call {@link #close()} when the facade is no longer needed to release
 * native parser resources.
 */
public final class AstParserFacade implements AutoCloseable {

    private static final Logger log = LoggerFactory.getLogger(AstParserFacade.class);

    private final AstParser parser;
    private final ScopeResolver scopeResolver;
    private final SymbolExtractor symbolExtractor;
    private final ScopeAwareHasher hasher;
    private final ParserPool parserPool;

    private AstParserFacade(ParserPool parserPool, AstParser parser, ScopeResolver scopeResolver,
                            SymbolExtractor symbolExtractor, ScopeAwareHasher hasher) {
        this.parserPool = parserPool;
        this.parser = parser;
        this.scopeResolver = scopeResolver;
        this.symbolExtractor = symbolExtractor;
        this.hasher = hasher;
    }

    /**
     * Create a facade with default configuration.
     * Parser pool size = available processors, timeout = 5s.
     */
    public static AstParserFacade createDefault() {
        ParserPool pool = new ParserPool();
        ScopeQueryRegistry queryRegistry = new ScopeQueryRegistry();
        queryRegistry.preloadAll();

        TreeSitterAstParser parser = new TreeSitterAstParser(pool);
        TreeSitterScopeResolver resolver = new TreeSitterScopeResolver(queryRegistry, pool);
        TreeSitterSymbolExtractor extractor = new TreeSitterSymbolExtractor(resolver);
        DefaultScopeAwareHasher hasher = new DefaultScopeAwareHasher();

        return new AstParserFacade(pool, parser, resolver, extractor, hasher);
    }

    /**
     * Create a facade with custom pool settings.
     *
     * @param poolSize  max parsers per language
     * @param timeoutMs borrow timeout in milliseconds
     */
    public static AstParserFacade create(int poolSize, long timeoutMs) {
        ParserPool pool = new ParserPool(poolSize, timeoutMs);
        ScopeQueryRegistry queryRegistry = new ScopeQueryRegistry();
        queryRegistry.preloadAll();

        TreeSitterAstParser parser = new TreeSitterAstParser(pool);
        TreeSitterScopeResolver resolver = new TreeSitterScopeResolver(queryRegistry, pool);
        TreeSitterSymbolExtractor extractor = new TreeSitterSymbolExtractor(resolver);
        DefaultScopeAwareHasher hasher = new DefaultScopeAwareHasher();

        return new AstParserFacade(pool, parser, resolver, extractor, hasher);
    }

    // ── High-level API ───────────────────────────────────────────────────

    /**
     * Check if a file path is supported for AST parsing.
     */
    public boolean isSupported(String filePath) {
        return SupportedLanguage.isSupported(filePath);
    }

    /**
     * Parse a file and return the parsed tree.
     * Caller must close the returned tree.
     */
    public ParsedTree parse(String filePath, String sourceText) {
        SupportedLanguage lang = resolveLanguage(filePath);
        return parser.parse(sourceText, lang);
    }

    /**
     * Resolve the innermost scope at a given line in a file.
     *
     * @param filePath   file path (for language detection)
     * @param sourceText full file content
     * @param line       1-based line number
     * @return the innermost scope, or empty for unsupported files or module-level lines
     */
    public Optional<ScopeInfo> resolveInnermostScope(String filePath, String sourceText, int line) {
        if (!isSupported(filePath)) return Optional.empty();
        try (ParsedTree tree = parse(filePath, sourceText)) {
            return scopeResolver.innermostScopeAt(tree, line);
        }
    }

    /**
     * Resolve the full scope chain at a given line (innermost to outermost).
     */
    public List<ScopeInfo> resolveScopeChain(String filePath, String sourceText, int line) {
        if (!isSupported(filePath)) return List.of();
        try (ParsedTree tree = parse(filePath, sourceText)) {
            return scopeResolver.scopeChainAt(tree, line);
        }
    }

    /**
     * Resolve all scopes in a file.
     */
    public List<ScopeInfo> resolveAllScopes(String filePath, String sourceText) {
        if (!isSupported(filePath)) return List.of();
        try (ParsedTree tree = parse(filePath, sourceText)) {
            return scopeResolver.resolveAll(tree);
        }
    }

    /**
     * Extract all symbols from a file.
     */
    public SymbolInfo extractSymbols(String filePath, String sourceText) {
        if (!isSupported(filePath)) return SymbolInfo.empty();
        try (ParsedTree tree = parse(filePath, sourceText)) {
            return symbolExtractor.extract(tree);
        }
    }

    /**
     * Compute a scope-aware content hash for tracking.
     * Uses the full scope boundaries from the AST.
     *
     * @param sourceText full file content
     * @param scope      AST-resolved scope
     * @return hex-encoded hash
     */
    public String computeScopeHash(String sourceText, ScopeInfo scope) {
        LineHashSequence lineHashes = LineHashSequence.from(sourceText);
        return hasher.hashScope(lineHashes, scope);
    }

    /**
     * Compute a context-aware hash around a specific line within its scope.
     *
     * @param sourceText full file content
     * @param line       1-based line number
     * @param scope      enclosing scope
     * @return hex-encoded context hash
     */
    public String computeContextHash(String sourceText, int line, ScopeInfo scope) {
        LineHashSequence lineHashes = LineHashSequence.from(sourceText);
        return hasher.hashContextInScope(lineHashes, line, scope);
    }

    // ── Direct access to components (for advanced consumers) ─────────────

    public AstParser getParser() { return parser; }
    public ScopeResolver getScopeResolver() { return scopeResolver; }
    public SymbolExtractor getSymbolExtractor() { return symbolExtractor; }
    public ScopeAwareHasher getHasher() { return hasher; }

    // ── Lifecycle ────────────────────────────────────────────────────────

    @Override
    public void close() {
        parserPool.close();
        log.info("AstParserFacade closed");
    }

    // ── Private helpers ──────────────────────────────────────────────────

    private static SupportedLanguage resolveLanguage(String filePath) {
        return SupportedLanguage.fromFilePath(filePath)
                .orElseThrow(() -> new AstParseException(
                        "Unsupported file type: " + filePath));
    }
}
