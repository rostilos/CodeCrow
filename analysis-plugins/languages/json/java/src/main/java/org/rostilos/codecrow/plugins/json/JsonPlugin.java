package org.rostilos.codecrow.plugins.json;

import org.rostilos.codecrow.plugins.ClasspathSyntaxPlugin;
import org.treesitter.TreeSitterJson;

import java.util.List;

public final class JsonPlugin extends ClasspathSyntaxPlugin {
    public JsonPlugin() {
        super(JsonPlugin.class, "json", List.of(".json"),
                "", TreeSitterJson::new);
    }
}
