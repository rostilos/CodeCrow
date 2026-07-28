package org.rostilos.codecrow.plugins.typescript;

import org.rostilos.codecrow.plugins.ClasspathSyntaxPlugin;
import org.treesitter.TreeSitterTypescript;

import java.util.List;

public final class TypescriptPlugin extends ClasspathSyntaxPlugin {
    public TypescriptPlugin() {
        super(TypescriptPlugin.class, "typescript", List.of(".cts", ".mts", ".ts"),
                "META-INF/codecrow/plugins/typescript/scopes.scm", TreeSitterTypescript::new);
    }
}
