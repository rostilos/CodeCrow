package org.rostilos.codecrow.astparser.internal;

import org.rostilos.codecrow.astparser.model.SupportedLanguage;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.HashMap;
import java.util.Map;
import java.util.Optional;

/**
 * Registry for per-language S-expression scope queries.
 * <p>
 * Loads {@code .scm} queries exclusively through syntax plugins and caches them.
 * <p>
 * Each query file contains tree-sitter S-expression patterns that match
 * scope-defining nodes (functions, classes, blocks, namespaces) with
 * named captures like {@code @function.def}, {@code @class.def}, etc.
 *
 * <h3>Thread safety</h3>
 * Fully thread-safe. Query strings are loaded once and cached by language ID.
 */
public final class ScopeQueryRegistry {

    private static final Logger log = LoggerFactory.getLogger(ScopeQueryRegistry.class);

    private final Map<SupportedLanguage, String> cache = new HashMap<>();
    private final PluginSyntaxRegistry pluginSyntaxRegistry;

    public ScopeQueryRegistry() {
        this(PluginSyntaxRegistry.discover());
    }

    public ScopeQueryRegistry(PluginSyntaxRegistry pluginSyntaxRegistry) {
        this.pluginSyntaxRegistry = pluginSyntaxRegistry;
    }

    /**
     * Get the S-expression scope query for a language.
     *
     * @param language the target language
     * @return the query string, or empty if no query file exists for this language
     */
    public Optional<String> getQuery(SupportedLanguage language) {
        // Fast path: already cached
        String cached = cache.get(language);
        if (cached != null) {
            return Optional.of(cached);
        }

        // Slow path: load from classpath
        synchronized (this) {
            // Double-check under lock
            cached = cache.get(language);
            if (cached != null) {
                return Optional.of(cached);
            }

            Optional<String> pluginQuery = pluginSyntaxRegistry.scopeQuery(language);
            if (pluginQuery.isPresent()) {
                cache.put(language, pluginQuery.get());
                log.debug("Loaded scope query for {} from syntax plugin", language);
                return pluginQuery;
            }

            Optional<String> query = pluginSyntaxRegistry.scopeQuery(language);
            if (query.isPresent()) {
                cache.put(language, query.get());
                log.debug("Loaded scope query for {} from syntax plugin", language);
            }
            return query;
        }
    }

    /**
     * Check if a scope query is available for the given language.
     */
    public boolean hasQuery(SupportedLanguage language) {
        return getQuery(language).isPresent();
    }

    /**
     * Preload all available scope queries. Call at startup for fail-fast behavior.
     *
     * @return number of queries successfully loaded
     */
    public int preloadAll() {
        int count = 0;
        for (SupportedLanguage lang : pluginSyntaxRegistry.languages()) {
            if (getQuery(lang).isPresent()) {
                count++;
            }
        }
        log.info("Preloaded scope queries for {}/{} syntax plugins",
                count, pluginSyntaxRegistry.languages().size());
        return count;
    }
}
