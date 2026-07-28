package org.rostilos.codecrow.plugins;

/** Optional language-plugin contract used by syntax-aware hosts. */
public interface SyntaxPlugin extends CodeCrowPlugin {
    PluginOutcome<SyntaxContribution> syntaxContribution();
}
