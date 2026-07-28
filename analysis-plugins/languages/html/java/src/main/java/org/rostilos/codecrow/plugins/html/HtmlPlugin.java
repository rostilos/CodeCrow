package org.rostilos.codecrow.plugins.html;

import org.rostilos.codecrow.plugins.ClasspathSyntaxPlugin;
import org.treesitter.TreeSitterHtml;

import java.util.List;

public final class HtmlPlugin extends ClasspathSyntaxPlugin {
    public HtmlPlugin() {
        super(HtmlPlugin.class, "html", List.of(".htm", ".html"),
                "", TreeSitterHtml::new);
    }
}
