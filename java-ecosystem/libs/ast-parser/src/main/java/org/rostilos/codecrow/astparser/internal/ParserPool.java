package org.rostilos.codecrow.astparser.internal;

import org.rostilos.codecrow.astparser.api.AstParseException;
import org.rostilos.codecrow.astparser.model.SupportedLanguage;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.treesitter.TSLanguage;
import org.treesitter.TSParser;

import java.util.EnumMap;
import java.util.Map;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.TimeUnit;

/**
 * Thread-safe pool of {@link TSParser} instances, keyed by language.
 * <p>
 * Tree-sitter's {@link TSParser} is <b>not thread-safe</b>: a single parser instance
 * cannot be used from multiple threads concurrently. This pool maintains a bounded
 * queue of parsers per language. Callers {@link #borrow(SupportedLanguage) borrow}
 * a parser, use it, and {@link #release(SupportedLanguage, TSParser) release} it back.
 * <p>
 * Parsers are created lazily on first borrow. The pool is bounded to prevent
 * unbounded native memory growth.
 *
 * <h3>Usage</h3>
 * <pre>{@code
 * TSParser parser = pool.borrow(SupportedLanguage.JAVA);
 * try {
 *     TSTree tree = parser.parseString(null, source);
 *     // use tree...
 * } finally {
 *     pool.release(SupportedLanguage.JAVA, parser);
 * }
 * }</pre>
 */
public final class ParserPool implements AutoCloseable {

    private static final Logger log = LoggerFactory.getLogger(ParserPool.class);

    /** Maximum parsers per language. */
    private final int poolSize;

    /** Borrow timeout. */
    private final long timeoutMs;

    /** Per-language parser queues. Lazily populated. */
    private final Map<SupportedLanguage, BlockingQueue<TSParser>> pools;

    /** Per-language grammar cache (TSLanguage is thread-safe and reusable). */
    private final Map<SupportedLanguage, TSLanguage> grammars;

    private volatile boolean closed = false;

    /**
     * @param poolSize  maximum parsers per language (recommend CPU cores × 2)
     * @param timeoutMs maximum wait time for a parser before throwing
     */
    public ParserPool(int poolSize, long timeoutMs) {
        if (poolSize < 1) throw new IllegalArgumentException("poolSize must be >= 1");
        if (timeoutMs < 0) throw new IllegalArgumentException("timeoutMs must be >= 0");
        this.poolSize = poolSize;
        this.timeoutMs = timeoutMs;
        this.pools = new EnumMap<>(SupportedLanguage.class);
        this.grammars = new EnumMap<>(SupportedLanguage.class);
    }

    /** Convenience constructor: pool size = availableProcessors, timeout = 5 seconds. */
    public ParserPool() {
        this(Runtime.getRuntime().availableProcessors(), 5000);
    }

    /**
     * Borrow a parser configured for the given language.
     * <p>
     * If the pool for this language is empty and below capacity, a new parser is created.
     * If at capacity, blocks until one is returned or timeout is reached.
     *
     * @param language the target language
     * @return a ready-to-use parser (caller MUST release after use)
     * @throws AstParseException if pool is closed, timeout exceeded, or grammar fails to load
     */
    public TSParser borrow(SupportedLanguage language) {
        if (closed) {
            throw new AstParseException("ParserPool is closed");
        }

        BlockingQueue<TSParser> queue = pools.computeIfAbsent(language,
                lang -> new ArrayBlockingQueue<>(poolSize));

        // Try to get an existing parser without blocking
        TSParser parser = queue.poll();
        if (parser != null) {
            return parser;
        }

        // No parser available — create a new one if below capacity
        synchronized (queue) {
            // Re-check after acquiring lock
            parser = queue.poll();
            if (parser != null) {
                return parser;
            }
            // Create new parser
            return createParser(language);
        }
    }

    /**
     * Return a parser to the pool. If the pool is full, the parser is closed.
     *
     * @param language the language the parser was configured for
     * @param parser   the parser to return
     */
    public void release(SupportedLanguage language, TSParser parser) {
        if (parser == null) return;
        if (closed) {
            parser.close();
            return;
        }
        BlockingQueue<TSParser> queue = pools.get(language);
        if (queue == null || !queue.offer(parser)) {
            parser.close();
        }
    }

    /**
     * Get or create the TSLanguage for a given SupportedLanguage.
     * TSLanguage instances are thread-safe and can be shared.
     */
    TSLanguage getGrammar(SupportedLanguage language) {
        return grammars.computeIfAbsent(language, lang -> {
            try {
                return lang.createGrammar();
            } catch (Exception e) {
                throw new AstParseException(
                        "Failed to load tree-sitter grammar for " + lang.name(), e);
            }
        });
    }

    private TSParser createParser(SupportedLanguage language) {
        TSLanguage grammar = getGrammar(language);
        TSParser parser = new TSParser();
        parser.setLanguage(grammar);
        log.trace("Created new TSParser for {}", language);
        return parser;
    }

    @Override
    public void close() {
        closed = true;
        for (Map.Entry<SupportedLanguage, BlockingQueue<TSParser>> entry : pools.entrySet()) {
            BlockingQueue<TSParser> queue = entry.getValue();
            TSParser parser;
            while ((parser = queue.poll()) != null) {
                try {
                    parser.close();
                } catch (Exception e) {
                    log.warn("Error closing parser for {}: {}", entry.getKey(), e.getMessage());
                }
            }
        }
        pools.clear();
        grammars.clear();
        log.debug("ParserPool closed");
    }
}
