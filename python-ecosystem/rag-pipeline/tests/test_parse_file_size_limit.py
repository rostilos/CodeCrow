"""File-size ceiling coverage for the non-indexing parse endpoints."""

from unittest.mock import patch


def _request(path: str, content: str):
    from rag_pipeline.api.models import ParseFileRequest

    return ParseFileRequest(path=path, content=content, language="python")


class TestParseFileSizeLimit:
    @patch(
        "rag_pipeline.api.routers.parse._configured_max_file_size_bytes",
        return_value=3,
    )
    def test_utf8_oversized_file_is_omitted_before_parser_construction(
        self,
        _configured_limit,
    ):
        from rag_pipeline.api.routers.parse import parse_file

        with patch(
            "rag_pipeline.api.routers.parse._parse_admitted_file",
        ) as parse_admitted_file:
            result = parse_file(_request("src/large.py", "éé"))

        assert result.success is False
        assert result.error == (
            "repository_file_size_limit_exceeded: file omitted as a whole "
            "without truncation; size_bytes=4 max_file_size_bytes=3"
        )
        parse_admitted_file.assert_not_called()

    @patch(
        "rag_pipeline.api.routers.parse._configured_max_file_size_bytes",
        return_value=4,
    )
    def test_exact_utf8_boundary_is_parsed(
        self,
        _configured_limit,
    ):
        from rag_pipeline.api.models import ParsedFileMetadata
        from rag_pipeline.api.routers.parse import parse_file

        request = _request("src/exact.py", "éé")
        with patch(
            "rag_pipeline.api.routers.parse._parse_admitted_file",
            return_value=ParsedFileMetadata(path=request.path),
        ) as parse_admitted_file:
            result = parse_file(request)

        assert result.success is True
        parse_admitted_file.assert_called_once_with(request)

    @patch(
        "rag_pipeline.api.routers.parse._configured_max_file_size_bytes",
        return_value=3,
    )
    def test_batch_reports_oversized_file_and_continues_with_other_files(
        self,
        _configured_limit,
    ):
        from rag_pipeline.api.models import ParseBatchRequest, ParsedFileMetadata
        from rag_pipeline.api.routers.parse import parse_files_batch

        with patch(
            "rag_pipeline.api.routers.parse._parse_admitted_file",
            return_value=ParsedFileMetadata(path="src/small.py"),
        ) as parse_admitted_file:
            payload = parse_files_batch(ParseBatchRequest(files=[
                _request("src/large.py", "éé"),
                _request("src/small.py", "abc"),
            ]))

        assert [result.success for result in payload["results"]] == [False, True]
        assert payload["summary"] == {
            "total": 2,
            "successful": 1,
            "failed": 1,
        }
        assert "repository_file_size_limit_exceeded" in (
            payload["results"][0].error or ""
        )
        parse_admitted_file.assert_called_once()
        assert parse_admitted_file.call_args.args[0].path == "src/small.py"
