package org.rostilos.codecrow.plugins.go;

import org.rostilos.codecrow.plugins.ClasspathSyntaxPlugin;
import org.treesitter.TreeSitterGo;

import java.util.List;

public final class GoPlugin extends ClasspathSyntaxPlugin {
    public GoPlugin() {
        super(GoPlugin.class, "go", List.of(".go"),
                "META-INF/codecrow/plugins/go/scopes.scm", TreeSitterGo::new);
    }
}
