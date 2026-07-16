package org.rostilos.codecrow.vcsclient.diff;

import org.junit.jupiter.api.Test;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.rostilos.codecrow.vcsclient.diff.ExactDiffInventory.ChangeStatus.ADD;
import static org.rostilos.codecrow.vcsclient.diff.ExactDiffInventory.ChangeStatus.COPY;
import static org.rostilos.codecrow.vcsclient.diff.ExactDiffInventory.ChangeStatus.DELETE;
import static org.rostilos.codecrow.vcsclient.diff.ExactDiffInventory.ChangeStatus.MODIFY;
import static org.rostilos.codecrow.vcsclient.diff.ExactDiffInventory.ChangeStatus.RENAME;
import static org.rostilos.codecrow.vcsclient.diff.ExactDiffInventory.Completeness.COMPLETE;
import static org.rostilos.codecrow.vcsclient.diff.ExactDiffInventory.Completeness.INCOMPLETE;
import static org.rostilos.codecrow.vcsclient.diff.ExactDiffInventory.GapType.MALFORMED;
import static org.rostilos.codecrow.vcsclient.diff.ExactDiffInventory.GapType.PATCH_UNAVAILABLE;
import static org.rostilos.codecrow.vcsclient.diff.ExactDiffInventory.GapType.PROVIDER_TRUNCATED;

class ExactDiffInventoryParserTest {

    private final ExactDiffInventoryParser parser = new ExactDiffInventoryParser();

    @Test
    void parsesAllChangeKindsIntoCanonicalPathOrder() {
        String rawDiff = """
                diff --git "a/old folder/naïve.txt" "b/new folder/你好.txt"
                similarity index 91%
                rename from "old folder/naïve.txt"
                rename to "new folder/你好.txt"
                diff --git a/src/App.java b/src/App.java
                old mode 100644
                new mode 100755
                --- a/src/App.java
                +++ b/src/App.java
                @@ -10,2 +10,3 @@ public class App {
                 old context
                -old value
                +new value
                +another value
                diff --git a/docs/obsolete.md b/docs/obsolete.md
                deleted file mode 100644
                --- a/docs/obsolete.md
                +++ /dev/null
                @@ -1 +0,0 @@
                -obsolete
                diff --git a/docs/source.md b/docs/copy.md
                similarity index 100%
                copy from docs/source.md
                copy to docs/copy.md
                diff --git a/assets/logo.bin b/assets/logo.bin
                new file mode 100644
                index 0000000..0123456
                Binary files /dev/null and b/assets/logo.bin differ
                """;

        ExactDiffInventory inventory = parser.parse(rawDiff);

        assertThat(inventory.completeness()).isEqualTo(COMPLETE);
        assertThat(inventory.gaps()).isEmpty();
        assertThat(inventory.entries())
                .extracting(ExactDiffInventory.Entry::status)
                .containsExactly(ADD, COPY, DELETE, RENAME, MODIFY);

        ExactDiffInventory.Entry added = inventory.entries().get(0);
        assertThat(added.oldPath()).isNull();
        assertThat(added.newPath()).isEqualTo("assets/logo.bin");
        assertThat(added.binary()).isTrue();
        assertThat(added.oldMode()).isNull();
        assertThat(added.newMode()).isEqualTo("100644");

        ExactDiffInventory.Entry copied = inventory.entries().get(1);
        assertThat(copied.oldPath()).isEqualTo("docs/source.md");
        assertThat(copied.newPath()).isEqualTo("docs/copy.md");
        assertThat(copied.status()).isEqualTo(COPY);

        ExactDiffInventory.Entry deleted = inventory.entries().get(2);
        assertThat(deleted.oldPath()).isEqualTo("docs/obsolete.md");
        assertThat(deleted.newPath()).isNull();
        assertThat(deleted.status()).isEqualTo(DELETE);
        assertThat(deleted.oldMode()).isEqualTo("100644");
        assertThat(deleted.newMode()).isNull();

        ExactDiffInventory.Entry renamed = inventory.entries().get(3);
        assertThat(renamed.oldPath()).isEqualTo("old folder/naïve.txt");
        assertThat(renamed.newPath()).isEqualTo("new folder/你好.txt");
        assertThat(renamed.status()).isEqualTo(RENAME);

        ExactDiffInventory.Entry modified = inventory.entries().get(4);
        assertThat(modified.oldPath()).isEqualTo("src/App.java");
        assertThat(modified.newPath()).isEqualTo("src/App.java");
        assertThat(modified.status()).isEqualTo(MODIFY);
        assertThat(modified.oldMode()).isEqualTo("100644");
        assertThat(modified.newMode()).isEqualTo("100755");
        assertThat(modified.hunks()).containsExactly(
                new ExactDiffInventory.Hunk(
                        new ExactDiffInventory.LineRange(10, 2),
                        new ExactDiffInventory.LineRange(10, 3)
                )
        );
    }

    @Test
    void preservesWholeRawUtf8ProvenanceAndExactPerEntryPatchDigest() {
        String rawDiff = """
                diff --git "a/资料/hello world.txt" "b/资料/hello world.txt"
                index 1111111..2222222 100644
                --- "a/资料/hello world.txt"
                +++ "b/资料/hello world.txt"
                @@ -1 +1 @@
                -before
                +after
                """;

        ExactDiffInventory inventory = parser.parse(rawDiff);

        assertThat(inventory.provenance().algorithm()).isEqualTo("SHA-256");
        assertThat(inventory.provenance().digest()).isEqualTo(sha256(rawDiff));
        assertThat(inventory.provenance().utf8ByteLength())
                .isEqualTo(rawDiff.getBytes(StandardCharsets.UTF_8).length);
        assertThat(inventory.entries()).singleElement().satisfies(entry -> {
            assertThat(entry.oldPath()).isEqualTo("资料/hello world.txt");
            assertThat(entry.newPath()).isEqualTo("资料/hello world.txt");
            assertThat(entry.rawPatchSha256()).isEqualTo(sha256(rawDiff));
        });
    }

    @Test
    void returnsAnExplicitCompleteInventoryForAnAuthoritativelyEmptyDiff() {
        ExactDiffInventory inventory = parser.parse("");

        assertThat(inventory.completeness()).isEqualTo(COMPLETE);
        assertThat(inventory.entries()).isEmpty();
        assertThat(inventory.gaps()).isEmpty();
        assertThat(inventory.provenance().digest()).isEqualTo(sha256(""));
        assertThat(inventory.provenance().utf8ByteLength()).isZero();
    }

    @Test
    void rejectsNonBlankInputWithoutAValidFileSectionAsMalformedNotEmpty() {
        ExactDiffInventory inventory = parser.parse("provider returned an HTML error page\n");

        assertThat(inventory.completeness()).isEqualTo(INCOMPLETE);
        assertThat(inventory.entries()).isEmpty();
        assertThat(inventory.gaps())
                .extracting(ExactDiffInventory.Gap::type)
                .containsExactly(MALFORMED);
    }

    @Test
    void rejectsWhitespaceAndHeaderOnlyResponsesAsMalformedNotEmpty() {
        for (String rawDiff : List.of(
                " \n\t",
                "diff --git a/src/App.java b/src/App.java\nupstream stopped here\n")) {
            ExactDiffInventory inventory = parser.parse(rawDiff);

            assertThat(inventory.completeness()).isEqualTo(INCOMPLETE);
            assertThat(inventory.gaps())
                    .extracting(ExactDiffInventory.Gap::type)
                    .contains(MALFORMED);
        }
    }

    @Test
    void retainsTheFileAndMarksAMalformedHunkIncomplete() {
        String rawDiff = """
                diff --git a/src/App.java b/src/App.java
                --- a/src/App.java
                +++ b/src/App.java
                @@ this is not a range @@
                -before
                +after
                """;

        ExactDiffInventory inventory = parser.parse(rawDiff);

        assertThat(inventory.completeness()).isEqualTo(INCOMPLETE);
        assertThat(inventory.entries()).singleElement().satisfies(entry -> {
            assertThat(entry.oldPath()).isEqualTo("src/App.java");
            assertThat(entry.newPath()).isEqualTo("src/App.java");
            assertThat(entry.hunks()).isEmpty();
        });
        assertThat(inventory.gaps())
                .extracting(ExactDiffInventory.Gap::type)
                .contains(MALFORMED);
    }

    @Test
    void conflictingHeaderAndPatchPathsAreMalformedInsteadOfChangingScope() {
        String rawDiff = """
                diff --git a/src/Safe.java b/src/Safe.java
                --- a/src/Safe.java
                +++ b/src/Other.java
                @@ -1 +1 @@
                -before
                +after
                """;

        ExactDiffInventory inventory = parser.parse(rawDiff);

        assertThat(inventory.completeness()).isEqualTo(INCOMPLETE);
        assertThat(inventory.gaps())
                .extracting(ExactDiffInventory.Gap::type)
                .contains(MALFORMED);
        assertThat(inventory.entries()).singleElement().satisfies(entry -> {
            assertThat(entry.oldPath()).isEqualTo("src/Safe.java");
            assertThat(entry.newPath()).isEqualTo("src/Safe.java");
        });
    }

    @Test
    void carriesProviderTruncationAndUnavailablePatchAsTypedIncompleteGaps() {
        String rawDiff = """
                diff --git a/src/App.java b/src/App.java
                index 1111111..2222222 100644
                --- a/src/App.java
                +++ b/src/App.java
                @@ -1 +1 @@
                -before
                +after
                """;
        List<ExactDiffInventory.Gap> declaredGaps = List.of(
                new ExactDiffInventory.Gap(PROVIDER_TRUNCATED, "comparison response was truncated"),
                new ExactDiffInventory.Gap(PATCH_UNAVAILABLE, "src/Missing.java")
        );

        ExactDiffInventory inventory = parser.parse(rawDiff, declaredGaps);

        assertThat(inventory.completeness()).isEqualTo(INCOMPLETE);
        assertThat(inventory.entries()).hasSize(1);
        assertThat(inventory.gaps()).containsExactlyElementsOf(declaredGaps);
    }

    private static String sha256(String value) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(value.getBytes(StandardCharsets.UTF_8));
            return java.util.HexFormat.of().formatHex(digest);
        } catch (NoSuchAlgorithmException exception) {
            throw new AssertionError("The JDK must provide SHA-256", exception);
        }
    }
}
