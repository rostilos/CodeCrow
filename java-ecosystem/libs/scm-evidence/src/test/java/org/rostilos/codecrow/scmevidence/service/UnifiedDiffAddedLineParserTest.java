package org.rostilos.codecrow.scmevidence.service;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class UnifiedDiffAddedLineParserTest {
    @Test
    void capturesRenamedTargetPathAndExactNewLineCoordinates() {
        String diff = """
                diff --git a/old/A.java b/new/A.java
                similarity index 80%
                rename from old/A.java
                rename to new/A.java
                --- a/old/A.java
                +++ b/new/A.java
                @@ -7,2 +20,4 @@
                 keep();
                -old();
                +introducedByAlice();
                +secondLine();
                 tail();
                """;

        var lines = new UnifiedDiffAddedLineParser().parse(diff);

        assertThat(lines).hasSize(2);
        assertThat(lines.get(0).filePath()).isEqualTo("new/A.java");
        assertThat(lines.get(0).lineNumber()).isEqualTo(21);
        assertThat(lines.get(0).lineHash())
                .isEqualTo(PatchIdentity.lineSha256("introducedByAlice();"));
        assertThat(lines.get(1).lineNumber()).isEqualTo(22);
    }
}
