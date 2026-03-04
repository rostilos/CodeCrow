package org.rostilos.codecrow.astparser.model;

import org.treesitter.*;

import java.util.*;
import java.util.function.Supplier;

/**
 * Supported programming languages for AST parsing.
 * <p>
 * Each member maps to:
 * <ul>
 *   <li>A set of file extensions (lowercase, without dot)</li>
 *   <li>A tree-sitter grammar factory (lazy — created only on first parse)</li>
 *   <li>A classpath location for the S-expression scope query</li>
 * </ul>
 *
 * <h3>Adding a new language</h3>
 * 1. Add the grammar JAR to {@code pom.xml}<br>
 * 2. Add a member here with its extensions and grammar factory<br>
 * 3. Create {@code queries/<lang>/scopes.scm} on the classpath<br>
 *
 * <h3>Thread safety</h3>
 * Enum instances are inherently safe. The grammar factories produce new instances
 * (which are themselves immutable) — the caller is responsible for pooling parsers.
 */
public enum SupportedLanguage {

    JAVA(Set.of("java"), TreeSitterJava::new),
    PYTHON(Set.of("py", "pyi"), TreeSitterPython::new),
    JAVASCRIPT(Set.of("js", "mjs", "cjs"), TreeSitterJavascript::new),
    TYPESCRIPT(Set.of("ts", "mts", "cts"), TreeSitterTypescript::new),
    TSX(Set.of("tsx"), TreeSitterTypescript::new),
    GO(Set.of("go"), TreeSitterGo::new),
    RUST(Set.of("rs"), TreeSitterRust::new),
    C(Set.of("c", "h"), TreeSitterC::new),
    CPP(Set.of("cpp", "cc", "cxx", "hpp", "hxx", "hh"), TreeSitterCpp::new),
    C_SHARP(Set.of("cs"), TreeSitterCSharp::new),
    PHP(Set.of("php"), TreeSitterPhp::new),
    RUBY(Set.of("rb"), TreeSitterRuby::new),
    SCALA(Set.of("scala", "sc"), TreeSitterScala::new),
    BASH(Set.of("sh", "bash", "zsh"), TreeSitterBash::new),
    HASKELL(Set.of("hs", "lhs"), TreeSitterHaskell::new),
    CSS(Set.of("css"), TreeSitterCss::new),
    HTML(Set.of("html", "htm"), TreeSitterHtml::new),
    JSON(Set.of("json"), TreeSitterJson::new);

    private final Set<String> extensions;
    private final Supplier<TSLanguage> grammarFactory;

    /** Reverse index: extension → language. Built once at class-load. */
    private static final Map<String, SupportedLanguage> EXTENSION_INDEX;

    static {
        Map<String, SupportedLanguage> idx = new HashMap<>();
        for (SupportedLanguage lang : values()) {
            for (String ext : lang.extensions) {
                idx.put(ext, lang);
            }
        }
        EXTENSION_INDEX = Collections.unmodifiableMap(idx);
    }

    SupportedLanguage(Set<String> extensions, Supplier<TSLanguage> grammarFactory) {
        this.extensions = Collections.unmodifiableSet(extensions);
        this.grammarFactory = grammarFactory;
    }

    /** File extensions recognized for this language (lowercase, no dot). */
    public Set<String> getExtensions() {
        return extensions;
    }

    /** Create a new TSLanguage instance. Caller is responsible for lifecycle. */
    public TSLanguage createGrammar() {
        return grammarFactory.get();
    }

    /** Classpath path for the S-expression scope query file. */
    public String getScopeQueryPath() {
        return "queries/" + name().toLowerCase() + "/scopes.scm";
    }

    /**
     * Resolve a language from a file extension (case-insensitive, without dot).
     *
     * @return the language, or {@link Optional#empty()} for unsupported extensions
     */
    public static Optional<SupportedLanguage> fromExtension(String extension) {
        if (extension == null || extension.isBlank()) {
            return Optional.empty();
        }
        return Optional.ofNullable(EXTENSION_INDEX.get(extension.toLowerCase().trim()));
    }

    /**
     * Resolve a language from a full file path.
     * Extracts the extension from the last {@code .} in the filename.
     *
     * @return the language, or {@link Optional#empty()} for unsupported files
     */
    public static Optional<SupportedLanguage> fromFilePath(String filePath) {
        if (filePath == null || filePath.isBlank()) {
            return Optional.empty();
        }
        int lastDot = filePath.lastIndexOf('.');
        if (lastDot < 0 || lastDot == filePath.length() - 1) {
            return Optional.empty();
        }
        String ext = filePath.substring(lastDot + 1);
        return fromExtension(ext);
    }

    /**
     * Check if a file path is supported for AST parsing.
     */
    public static boolean isSupported(String filePath) {
        return fromFilePath(filePath).isPresent();
    }
}
