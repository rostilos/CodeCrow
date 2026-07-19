"""Parity tests for paths accepted by the Java exact-diff inventory."""

import pytest

from utils.git_diff_paths import (
    GitDiffPathError,
    parse_git_diff_header,
    parse_git_marker_path,
)


def test_parses_unquoted_git_paths_without_splitting_embedded_spaces():
    assert parse_git_diff_header(
        "diff --git a/old folder/file.py b/new folder/file.py"
    ) == ("old folder/file.py", "new folder/file.py")
    assert parse_git_marker_path(
        "+++ b/new folder/file.py", "+++"
    ) == "new folder/file.py"


def test_decodes_c_style_octal_bytes_as_utf8_after_the_complete_token():
    header = (
        r'diff --git "a/old folder/na\303\257ve.py" '
        r'"b/new folder/\344\275\240\345\245\275.py"'
    )

    assert parse_git_diff_header(header) == (
        "old folder/naïve.py",
        "new folder/你好.py",
    )
    assert parse_git_marker_path(
        r'+++ "b/new folder/\344\275\240\345\245\275.py"', "+++"
    ) == "new folder/你好.py"


def test_rejects_malformed_quoted_utf8_instead_of_inventing_a_path():
    with pytest.raises(GitDiffPathError, match="UTF-8"):
        parse_git_diff_header(r'diff --git "a/\377.py" "b/ok.py"')
