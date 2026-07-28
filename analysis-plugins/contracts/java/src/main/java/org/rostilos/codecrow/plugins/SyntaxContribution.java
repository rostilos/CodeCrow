package org.rostilos.codecrow.plugins;

import java.util.List;
import java.util.Objects;

/** Declarative syntax metadata supplied by a language plugin. */
public record SyntaxContribution(
        String languageId,
        List<String> extensions,
        String scopeQueryResource,
        SyntaxGrammarFactory grammarFactory) {

    public SyntaxContribution {
        languageId = PluginValues.requirePluginId(languageId, "language id");
        extensions = PluginValues.sortedUnique(extensions, "syntax extensions");
        if (extensions.stream().anyMatch(value -> !PluginValues.isExtension(value))) {
            throw new IllegalArgumentException("syntax extensions must be normalized lowercase extensions");
        }
        scopeQueryResource = scopeQueryResource == null ? "" : scopeQueryResource.trim();
        if (scopeQueryResource.startsWith("/") || scopeQueryResource.contains("..")) {
            throw new IllegalArgumentException("scope query resource must be a safe classpath-relative path");
        }
        grammarFactory = Objects.requireNonNull(grammarFactory, "grammar factory is required");
    }
}
