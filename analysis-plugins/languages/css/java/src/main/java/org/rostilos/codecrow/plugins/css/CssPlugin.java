package org.rostilos.codecrow.plugins.css;

import org.rostilos.codecrow.plugins.ClasspathSyntaxPlugin;
import org.treesitter.TreeSitterCss;

import java.util.List;

public final class CssPlugin extends ClasspathSyntaxPlugin {
    public CssPlugin() {
        super(CssPlugin.class, "css", List.of(".css"),
                "", TreeSitterCss::new);
    }
}
