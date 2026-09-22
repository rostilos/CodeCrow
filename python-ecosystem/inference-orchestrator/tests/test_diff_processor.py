"""Acquired changes must stay reviewable regardless of diff size or path syntax."""

import unittest

from utils.diff_processor import HunkDisposition, process_raw_diff


class DiffIngestionTest(unittest.TestCase):
    def test_keeps_all_files_and_hunks(self):
        sections = []
        for index in range(410):
            sections.append(
                f"diff --git a/src/f{index}.py b/src/f{index}.py\n"
                f"--- a/src/f{index}.py\n+++ b/src/f{index}.py\n"
                "@@ -1 +1 @@\n-old\n+new\n"
            )
        parsed = process_raw_diff("".join(sections))
        self.assertEqual(len(parsed.files), 410)
        self.assertEqual(sum(len(file.hunks) for file in parsed.files), 410)
        self.assertEqual(parsed.files[-1].hunks[0].disposition, HunkDisposition.REVIEWABLE)

    def test_quoted_path_and_deleted_hunk_keep_correct_location(self):
        parsed = process_raw_diff(
            'diff --git "a/src/old file.py" "b/src/old file.py"\n'
            'deleted file mode 100644\n--- "a/src/old file.py"\n+++ /dev/null\n'
            '@@ -12,2 +0,0 @@\n-old\n-code\n'
        )
        file = parsed.files[0]
        self.assertEqual(file.path, "src/old file.py")
        self.assertEqual(file.hunks[0].old_start, 12)
        self.assertEqual(file.hunks[0].disposition, HunkDisposition.DELETED)


if __name__ == "__main__":
    unittest.main()
