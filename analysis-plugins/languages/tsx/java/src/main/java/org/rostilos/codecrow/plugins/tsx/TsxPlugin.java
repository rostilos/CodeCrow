package org.rostilos.codecrow.plugins.tsx;

import org.rostilos.codecrow.plugins.ClasspathSyntaxPlugin;
import org.treesitter.TreeSitterTypescript;

import java.util.List;

public final class TsxPlugin extends ClasspathSyntaxPlugin {
    public TsxPlugin() {
        super(TsxPlugin.class, "tsx", List.of(".tsx"),
                "META-INF/codecrow/plugins/tsx/scopes.scm", TreeSitterTypescript::new);
    }
}
