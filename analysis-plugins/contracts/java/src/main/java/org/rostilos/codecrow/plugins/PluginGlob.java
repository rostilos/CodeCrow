package org.rostilos.codecrow.plugins;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.regex.Pattern;

public final class PluginGlob {
    private static final int CACHE_LIMIT = 512;
    private static final Map<String, Pattern> COMPILED = new LinkedHashMap<>(
            CACHE_LIMIT, 0.75f, true) {
        @Override
        protected boolean removeEldestEntry(Map.Entry<String, Pattern> eldest) {
            return size() > CACHE_LIMIT;
        }
    };

    private PluginGlob() {
    }

    public static boolean matches(String glob, String path) {
        return compiled(glob).matcher(path).matches();
    }

    private static Pattern compiled(String glob) {
        synchronized (COMPILED) {
            Pattern cached = COMPILED.get(glob);
            if (cached != null) return cached;
            Pattern created = compile(glob);
            COMPILED.put(glob, created);
            return created;
        }
    }

    private static Pattern compile(String glob) {
        StringBuilder regex = new StringBuilder("^");
        for (int index = 0; index < glob.length(); index++) {
            char character = glob.charAt(index);
            if (character == '*') {
                boolean recursive = index + 1 < glob.length() && glob.charAt(index + 1) == '*';
                if (recursive) {
                    regex.append(".*");
                    index++;
                } else {
                    regex.append("[^/]*");
                }
            } else if (character == '?') {
                regex.append("[^/]");
            } else {
                regex.append(Pattern.quote(String.valueOf(character)));
            }
        }
        return Pattern.compile(regex.append('$').toString());
    }
}
