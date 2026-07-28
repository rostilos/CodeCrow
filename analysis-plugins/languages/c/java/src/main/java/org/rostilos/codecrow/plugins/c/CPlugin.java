package org.rostilos.codecrow.plugins.c;

import org.rostilos.codecrow.plugins.ClasspathSyntaxPlugin;
import org.treesitter.TreeSitterC;

import java.util.List;

public final class CPlugin extends ClasspathSyntaxPlugin {
    public CPlugin() {
        super(CPlugin.class, "c", List.of(".c"),
                "META-INF/codecrow/plugins/c/scopes.scm", TreeSitterC::new);
    }
}
