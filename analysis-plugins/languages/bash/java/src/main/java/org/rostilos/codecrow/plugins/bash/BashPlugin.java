package org.rostilos.codecrow.plugins.bash;

import org.rostilos.codecrow.plugins.ClasspathSyntaxPlugin;
import org.treesitter.TreeSitterBash;

import java.util.List;

public final class BashPlugin extends ClasspathSyntaxPlugin {
    public BashPlugin() {
        super(BashPlugin.class, "bash", List.of(".bash", ".sh", ".zsh"),
                "META-INF/codecrow/plugins/bash/scopes.scm", TreeSitterBash::new);
    }
}
