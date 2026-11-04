package org.rostilos.codecrow.vcsclient;

import java.io.IOException;
import java.util.List;
import java.util.Map;
import java.util.LinkedHashMap;

/** Pinned local source evidence. Listed paths prove presence; omissions do not prove Git-tree absence. */
public interface RepositorySource {
    List<String> paths();
    String readFile(String path, int maxBytes) throws IOException;

    default Map<String, String> readFiles(List<String> paths, int maxBytes, long maxTotalBytes) throws IOException {
        Map<String, String> contents = new LinkedHashMap<>();
        long retained = 0;
        for (String path : paths) {
            if (Thread.currentThread().isInterrupted()) throw new java.io.InterruptedIOException("Source acquisition cancelled");
            if (retained >= maxTotalBytes) break;
            try {
                String content = readFile(path, (int) Math.min(maxBytes, maxTotalBytes - retained));
                if (content != null) {
                    retained += content.getBytes(java.nio.charset.StandardCharsets.UTF_8).length;
                    contents.put(path, content);
                }
            } catch (java.io.InterruptedIOException cancelled) {
                throw cancelled;
            } catch (IOException unavailable) {
                org.slf4j.LoggerFactory.getLogger(RepositorySource.class).warn(
                        "Local source unavailable for {}; retaining other files: {}", path, unavailable.getMessage());
            }
        }
        return contents;
    }
}
