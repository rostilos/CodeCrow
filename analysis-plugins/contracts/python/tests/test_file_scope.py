import json
from pathlib import Path

from codecrow_plugins.file_scope import EXCLUDED_EXTENSIONS, filter_diff, is_excluded, filter_records


def test_every_image_extension_is_excluded_case_insensitively_without_excluding_source():
    for extension in EXCLUDED_EXTENSIONS:
        assert is_excluded('assets/sub/photo.' + extension.upper())
        assert is_excluded('assets/photo.' + extension + '.gz')
        assert not is_excluded('assets/photo.' + extension + '.ts')
        assert not is_excluded('folder.' + extension + '/source.py')
    assert not is_excluded(None)
    assert not is_excluded('Dockerfile')


def test_shared_image_record_fixture_preserves_source_and_removes_owned_metadata():
    fixture = json.loads((Path(__file__).parents[2] / 'fixtures/image-exclusion.json').read_text())
    assert filter_records(fixture['input']) == fixture['expected']
    source = 'diff --git a/source.py b/source.py\n--- a/source.py\n+++ b/source.py\n@@ -0,0 +1 @@\n+image = "photo.png"\n'
    image = 'diff --git "a/assets/my logo.SVG" "b/assets/my logo.SVG"\n+<svg/>\n'
    renamed = 'diff --git a/logo.PNG b/other.txt\nrename from logo.PNG\nrename to other.txt\n'
    assert filter_diff(image + source + renamed) == source


def test_diff_paths_with_spaces_preserve_source_bytes():
    source = 'diff --git a/icon.png helper.py b/icon.png helper.py\r\n+keep\r\n'
    image = 'diff --git a/my logo.svg b/my logo.svg\r\n+<svg/>\r\n'
    assert filter_diff(source + image) == source
