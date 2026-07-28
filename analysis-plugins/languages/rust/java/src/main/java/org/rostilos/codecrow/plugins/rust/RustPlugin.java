package org.rostilos.codecrow.plugins.rust;

import org.rostilos.codecrow.plugins.ClasspathSyntaxPlugin;
import org.treesitter.TreeSitterRust;

import java.util.List;

public final class RustPlugin extends ClasspathSyntaxPlugin {
    public RustPlugin() {
        super(RustPlugin.class, "rust", List.of(".rs"),
                "META-INF/codecrow/plugins/rust/scopes.scm", TreeSitterRust::new);
    }
}
