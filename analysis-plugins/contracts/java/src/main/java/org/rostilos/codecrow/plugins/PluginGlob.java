package org.rostilos.codecrow.plugins;

import java.util.regex.Pattern;

public final class PluginGlob {
    private PluginGlob() {
    }

    public static boolean matches(String glob, String path) {
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
        return Pattern.compile(regex.append('$').toString()).matcher(path).matches();
    }
}
