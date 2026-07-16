package org.rostilos.codecrow.vcsclient.diff;

import static org.assertj.core.api.Assertions.assertThat;
import static org.rostilos.codecrow.vcsclient.diff.ExactDiffInventory.ChangeStatus.MODIFY;
import static org.rostilos.codecrow.vcsclient.diff.ExactDiffInventory.ChangeStatus.RENAME;
import static org.rostilos.codecrow.vcsclient.diff.ExactDiffInventory.Completeness.COMPLETE;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.List;
import java.util.stream.Stream;

import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;

class CrossProviderExactDiffInventoryContractTest {
    private static final List<SemanticEntry> EXPECTED = List.of(
            new SemanticEntry(
                    RENAME,
                    "docs/old name.md",
                    "docs/new name.md",
                    List.of(),
                    false,
                    null,
                    null),
            new SemanticEntry(
                    MODIFY,
                    "src/App.java",
                    "src/App.java",
                    List.of(new ExactDiffInventory.Hunk(
                            new ExactDiffInventory.LineRange(4, 2),
                            new ExactDiffInventory.LineRange(4, 2))),
                    false,
                    "100644",
                    "100755"));

    private final ExactDiffInventoryParser parser = new ExactDiffInventoryParser();

    @ParameterizedTest(name = "{0}")
    @MethodSource("providerDiffs")
    void equivalentProviderDiffsProduceOneCanonicalSemanticInventory(
            String provider,
            String rawDiff) {
        ExactDiffInventory inventory = parser.parse(rawDiff);

        assertThat(inventory.completeness()).as(provider).isEqualTo(COMPLETE);
        assertThat(inventory.gaps()).as(provider).isEmpty();
        assertThat(inventory.entries().stream().map(SemanticEntry::from).toList())
                .as(provider)
                .isEqualTo(EXPECTED);
    }

    @ParameterizedTest(name = "{0}")
    @MethodSource("providerDiffs")
    void provenanceBindsEachProvidersExactRawBytes(String provider, String rawDiff) {
        var provenance = parser.parse(rawDiff).provenance();
        byte[] rawBytes = rawDiff.getBytes(StandardCharsets.UTF_8);

        assertThat(provenance.algorithm()).as(provider).isEqualTo("SHA-256");
        assertThat(provenance.digest()).as(provider).isEqualTo(sha256(rawBytes));
        assertThat(provenance.utf8ByteLength()).as(provider).isEqualTo(rawBytes.length);
    }

    private static Stream<Arguments> providerDiffs() {
        return Stream.of(
                Arguments.of("GitHub", """
                        diff --git "a/docs/old name.md" "b/docs/new name.md"
                        similarity index 100%
                        rename from "docs/old name.md"
                        rename to "docs/new name.md"
                        diff --git a/src/App.java b/src/App.java
                        old mode 100644
                        new mode 100755
                        index 1111111..2222222
                        --- a/src/App.java
                        +++ b/src/App.java
                        @@ -4,2 +4,2 @@ public class App {
                        -    return "before";
                        +    return "after";
                         }
                        """),
                Arguments.of("GitLab", """
                        diff --git a/docs/old name.md b/docs/new name.md
                        rename from docs/old name.md
                        rename to docs/new name.md

                        diff --git a/src/App.java b/src/App.java
                        old mode 100644
                        new mode 100755
                        --- a/src/App.java
                        +++ b/src/App.java
                        @@ -4,2 +4,2 @@
                        -    return "before";
                        +    return "after";
                         }
                        """),
                Arguments.of("Bitbucket", """
                        diff --git "a/docs/old name.md" "b/docs/new name.md"
                        similarity index 100%
                        rename from docs/old name.md
                        rename to docs/new name.md
                        diff --git a/src/App.java b/src/App.java
                        index 1111111..2222222
                        old mode 100644
                        new mode 100755
                        --- a/src/App.java
                        +++ b/src/App.java
                        @@ -4,2 +4,2 @@
                        -    return "before";
                        +    return "after";
                         }
                        \\ No newline at end of file
                        """));
    }

    private static String sha256(byte[] value) {
        try {
            return java.util.HexFormat.of().formatHex(
                    MessageDigest.getInstance("SHA-256").digest(value));
        } catch (NoSuchAlgorithmException exception) {
            throw new AssertionError("The JDK must provide SHA-256", exception);
        }
    }

    private record SemanticEntry(
            ExactDiffInventory.ChangeStatus status,
            String oldPath,
            String newPath,
            List<ExactDiffInventory.Hunk> hunks,
            boolean binary,
            String oldMode,
            String newMode) {
        private static SemanticEntry from(ExactDiffInventory.Entry entry) {
            return new SemanticEntry(
                    entry.status(),
                    entry.oldPath(),
                    entry.newPath(),
                    entry.hunks(),
                    entry.binary(),
                    entry.oldMode(),
                    entry.newMode());
        }
    }
}
