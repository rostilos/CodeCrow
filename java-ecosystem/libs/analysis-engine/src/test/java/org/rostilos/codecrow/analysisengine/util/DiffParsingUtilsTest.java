package org.rostilos.codecrow.analysisengine.util;

import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class DiffParsingUtilsTest {

    @Test
    void parseFileChanges_shouldClassifyModifiedAddedDeletedAndRenamedFiles() {
        String diff = """
                diff --git a/src/Modified.java b/src/Modified.java
                index 1111111..2222222 100644
                --- a/src/Modified.java
                +++ b/src/Modified.java
                @@ -1,2 +1,3 @@
                 class Modified {}
                +// added
                diff --git a/src/New.java b/src/New.java
                new file mode 100644
                index 0000000..3333333
                --- /dev/null
                +++ b/src/New.java
                @@ -0,0 +1 @@
                +class New {}
                diff --git a/src/Deleted.java b/src/Deleted.java
                deleted file mode 100644
                index 4444444..0000000
                --- a/src/Deleted.java
                +++ /dev/null
                @@ -1 +0,0 @@
                -class Deleted {}
                diff --git a/src/OldName.java b/src/NewName.java
                similarity index 91%
                rename from src/OldName.java
                rename to src/NewName.java
                index 5555555..6666666 100644
                --- a/src/OldName.java
                +++ b/src/NewName.java
                @@ -10,3 +10,4 @@
                 class NewName {
                +    void added() {}
                 }
                """;

        List<DiffParsingUtils.FileChange> changes = DiffParsingUtils.parseFileChanges(diff);

        assertThat(changes).extracting(DiffParsingUtils.FileChange::changeType)
                .containsExactly(
                        DiffParsingUtils.ChangeType.MODIFIED,
                        DiffParsingUtils.ChangeType.ADDED,
                        DiffParsingUtils.ChangeType.DELETED,
                        DiffParsingUtils.ChangeType.RENAMED);

        assertThat(changes.get(0).oldPath()).isEqualTo("src/Modified.java");
        assertThat(changes.get(0).newPath()).isEqualTo("src/Modified.java");
        assertThat(changes.get(1).oldPath()).isNull();
        assertThat(changes.get(1).newPath()).isEqualTo("src/New.java");
        assertThat(changes.get(2).oldPath()).isEqualTo("src/Deleted.java");
        assertThat(changes.get(2).newPath()).isNull();
        assertThat(changes.get(3).oldPath()).isEqualTo("src/OldName.java");
        assertThat(changes.get(3).newPath()).isEqualTo("src/NewName.java");
        assertThat(changes.get(3).diff()).contains("rename from src/OldName.java");
    }
}
