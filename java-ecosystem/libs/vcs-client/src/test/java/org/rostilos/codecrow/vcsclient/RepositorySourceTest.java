package org.rostilos.codecrow.vcsclient;

import org.junit.jupiter.api.Test;
import java.io.IOException;
import java.util.List;
import static org.assertj.core.api.Assertions.*;

class RepositorySourceTest {
    @Test
    void localReadFailureRetainsOtherFilesAndByteBudgetStopsFurtherReads() throws Exception {
        var reads = new java.util.ArrayList<String>();
        RepositorySource source = new RepositorySource() {
            public List<String> paths() { return List.of("bad", "one", "two", "three"); }
            public String readFile(String path, int maxBytes) throws IOException {
                reads.add(path);
                if (path.equals("bad")) throw new IOException("unreadable text");
                return "é";
            }
        };
        assertThat(source.readFiles(source.paths(), 100, 4)).containsOnlyKeys("one", "two");
        assertThat(reads).containsExactly("bad", "one", "two");
        Thread.currentThread().interrupt();
        try {
            assertThatThrownBy(() -> source.readFiles(source.paths(), 100, 4))
                    .isInstanceOf(java.io.InterruptedIOException.class);
            assertThat(reads).hasSize(3);
        } finally { Thread.interrupted(); }
    }
}
