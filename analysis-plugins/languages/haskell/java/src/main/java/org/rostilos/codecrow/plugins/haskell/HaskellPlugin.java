package org.rostilos.codecrow.plugins.haskell;

import org.rostilos.codecrow.plugins.ClasspathSyntaxPlugin;
import org.treesitter.TreeSitterHaskell;

import java.util.List;

public final class HaskellPlugin extends ClasspathSyntaxPlugin {
    public HaskellPlugin() {
        super(HaskellPlugin.class, "haskell", List.of(".hs", ".lhs"),
                "META-INF/codecrow/plugins/haskell/scopes.scm", TreeSitterHaskell::new);
    }
}
