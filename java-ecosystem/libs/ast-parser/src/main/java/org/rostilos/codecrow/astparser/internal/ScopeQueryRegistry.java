package org.rostilos.codecrow.astparser.internal;

import org.rostilos.codecrow.astparser.model.SupportedLanguage;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.EnumMap;
import java.util.Map;
import java.util.Optional;
import java.util.stream.Collectors;

/**
 * Registry for per-language S-expression scope queries.
 * <p>
 * Loads {@code .scm} query files from the classpath under
 * {@code queries/<language>/scopes.scm} and caches them.
 * <p>
 * Each query file contains tree-sitter S-expression patterns that match
 * scope-defining nodes (functions, classes, blocks, namespaces) with
 * named captures like {@code @function.def}, {@code @class.def}, etc.
 *
 * <h3>Thread safety</h3>
 * Fully thread-safe. Query strings are loaded once and cached in an EnumMap.
 */
public final class ScopeQueryRegistry {

    private static final Logger log = LoggerFactory.getLogger(ScopeQueryRegistry.class);

    private final Map<SupportedLanguage, String> cache = new EnumMap<>(SupportedLanguage.class);

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

            String path = language.getScopeQueryPath();
            try (InputStream is = getClass().getClassLoader().getResourceAsStream(path)) {
                if (is == null) {
                    log.debug("No scope query found for {} at classpath:{}", language, path);
                    return Optional.empty();
                }
                String query = new BufferedReader(new InputStreamReader(is, StandardCharsets.UTF_8))
                        .lines()
                        .collect(Collectors.joining("\n"));

                if (query.isBlank()) {
                    log.warn("Empty scope query file for {} at classpath:{}", language, path);
                    return Optional.empty();
                }

                cache.put(language, query);
                log.debug("Loaded scope query for {} ({} chars)", language, query.length());
                return Optional.of(query);
            } catch (IOException e) {
                log.error("Failed to load scope query for {}: {}", language, e.getMessage());
                return Optional.empty();
            }
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
        for (SupportedLanguage lang : SupportedLanguage.values()) {
            if (getQuery(lang).isPresent()) {
                count++;
            }
        }
        log.info("Preloaded scope queries for {}/{} languages", count, SupportedLanguage.values().length);
        return count;
    }
}
