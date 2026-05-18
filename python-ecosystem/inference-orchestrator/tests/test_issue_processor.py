"""
Unit tests for service.review.issue_processor — post_process_analysis_result.
"""
from service.review.issue_processor import post_process_analysis_result


class TestPostProcessAnalysisResult:
    def test_passthrough(self):
        data = {"issues": [{"id": "1", "file": "a.py"}], "comment": "ok"}
        result = post_process_analysis_result(data)
        assert result is data

    def test_no_issues_key(self):
        data = {"comment": "no issues"}
        result = post_process_analysis_result(data)
        assert result is data

    def test_extra_kwargs_ignored(self):
        data = {"issues": []}
        result = post_process_analysis_result(
            data, diff_content="x", file_contents={}, previous_issues=[]
        )
        assert result is data
