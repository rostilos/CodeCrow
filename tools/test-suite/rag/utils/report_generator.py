#!/usr/bin/env python3
"""
Report Generator - Generate HTML and Markdown test reports.
"""
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional
import html


class ReportGenerator:
    """Generate test reports in various formats."""
    
    def __init__(self, results: Dict[str, Any]):
        self.results = results
    
    @classmethod
    def from_json(cls, filepath: Path) -> "ReportGenerator":
        """Create generator from JSON file."""
        with open(filepath) as f:
            results = json.load(f)
        return cls(results)
    
    def generate_html(self, output_path: Optional[Path] = None) -> str:
        """Generate HTML report."""
        timestamp = self.results.get("timestamp", datetime.now().isoformat())
        duration = self.results.get("duration_seconds", 0)
        config = self.results.get("config", {})
        summary = self.results.get("summary", {})
        
        total_passed = summary.get("total_passed", 0)
        total_failed = summary.get("total_failed", 0)
        total_tests = total_passed + total_failed
        pass_rate = (total_passed / total_tests * 100) if total_tests > 0 else 0
        
        status_class = "success" if total_failed == 0 else "warning" if total_passed > 0 else "failure"
        
        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>RAG Test Report - {timestamp[:10]}</title>
    <style>
        :root {{
            --success: #10b981;
            --warning: #f59e0b;
            --failure: #ef4444;
            --bg: #0f172a;
            --card: #1e293b;
            --text: #e2e8f0;
            --muted: #94a3b8;
        }}
        
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg);
            color: var(--text);
            padding: 2rem;
            line-height: 1.6;
        }}
        
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        
        h1 {{
            font-size: 2rem;
            margin-bottom: 0.5rem;
        }}
        
        h2 {{
            font-size: 1.25rem;
            margin: 1.5rem 0 1rem;
            color: var(--muted);
        }}
        
        .header {{
            margin-bottom: 2rem;
        }}
        
        .meta {{
            color: var(--muted);
            font-size: 0.875rem;
        }}
        
        .summary-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 1rem;
            margin-bottom: 2rem;
        }}
        
        .card {{
            background: var(--card);
            border-radius: 0.5rem;
            padding: 1.5rem;
        }}
        
        .card-title {{
            font-size: 0.875rem;
            color: var(--muted);
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }}
        
        .card-value {{
            font-size: 2rem;
            font-weight: 700;
            margin-top: 0.5rem;
        }}
        
        .success {{ color: var(--success); }}
        .warning {{ color: var(--warning); }}
        .failure {{ color: var(--failure); }}
        
        .suite {{
            background: var(--card);
            border-radius: 0.5rem;
            margin-bottom: 1rem;
            overflow: hidden;
        }}
        
        .suite-header {{
            padding: 1rem 1.5rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid rgba(255,255,255,0.1);
        }}
        
        .suite-name {{
            font-weight: 600;
            text-transform: uppercase;
        }}
        
        .suite-stats {{
            font-size: 0.875rem;
        }}
        
        .test-list {{
            padding: 1rem 1.5rem;
        }}
        
        .test-item {{
            display: flex;
            justify-content: space-between;
            padding: 0.5rem 0;
            border-bottom: 1px solid rgba(255,255,255,0.05);
        }}
        
        .test-item:last-child {{
            border-bottom: none;
        }}
        
        .test-name {{
            font-family: 'Monaco', 'Courier New', monospace;
            font-size: 0.875rem;
        }}
        
        .badge {{
            padding: 0.125rem 0.5rem;
            border-radius: 9999px;
            font-size: 0.75rem;
            font-weight: 500;
        }}
        
        .badge-pass {{
            background: rgba(16, 185, 129, 0.2);
            color: var(--success);
        }}
        
        .badge-fail {{
            background: rgba(239, 68, 68, 0.2);
            color: var(--failure);
        }}
        
        .config-table {{
            width: 100%;
            border-collapse: collapse;
        }}
        
        .config-table td {{
            padding: 0.5rem;
            border-bottom: 1px solid rgba(255,255,255,0.05);
        }}
        
        .config-table td:first-child {{
            color: var(--muted);
            width: 150px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>RAG Test Report</h1>
            <p class="meta">Generated: {timestamp} | Duration: {duration:.1f}s</p>
        </div>
        
        <div class="summary-grid">
            <div class="card">
                <div class="card-title">Total Tests</div>
                <div class="card-value">{total_tests}</div>
            </div>
            <div class="card">
                <div class="card-title">Passed</div>
                <div class="card-value success">{total_passed}</div>
            </div>
            <div class="card">
                <div class="card-title">Failed</div>
                <div class="card-value {'failure' if total_failed > 0 else 'success'}">{total_failed}</div>
            </div>
            <div class="card">
                <div class="card-title">Pass Rate</div>
                <div class="card-value {status_class}">{pass_rate:.0f}%</div>
            </div>
        </div>
        
        <h2>Configuration</h2>
        <div class="card">
            <table class="config-table">
                <tr><td>Workspace</td><td>{html.escape(str(config.get('workspace', 'N/A')))}</td></tr>
                <tr><td>Project</td><td>{html.escape(str(config.get('project', 'N/A')))}</td></tr>
                <tr><td>Branch</td><td>{html.escape(str(config.get('branch', 'N/A')))}</td></tr>
            </table>
        </div>
        
        <h2>Test Suites</h2>
        {self._generate_suites_html()}
    </div>
</body>
</html>"""
        
        if output_path:
            output_path.write_text(html_content)
        
        return html_content
    
    def _generate_suites_html(self) -> str:
        """Generate HTML for test suites."""
        suites_html = []
        
        for suite_name, suite_results in self.results.get("suites", {}).items():
            passed = suite_results.get("passed", 0)
            failed = suite_results.get("failed", 0)
            total = passed + failed
            
            status_class = "success" if failed == 0 else "warning" if passed > 0 else "failure"
            
            tests_html = []
            for test in suite_results.get("tests", []):
                test_name = html.escape(str(test.get("name", "unknown")))
                test_passed = test.get("passed", False)
                badge_class = "badge-pass" if test_passed else "badge-fail"
                badge_text = "PASS" if test_passed else "FAIL"
                
                tests_html.append(f'''
                <div class="test-item">
                    <span class="test-name">{test_name}</span>
                    <span class="badge {badge_class}">{badge_text}</span>
                </div>''')
            
            suite_html = f'''
        <div class="suite">
            <div class="suite-header">
                <span class="suite-name {status_class}">{html.escape(suite_name)}</span>
                <span class="suite-stats">{passed}/{total} passed</span>
            </div>
            <div class="test-list">
                {"".join(tests_html)}
            </div>
        </div>'''
            
            suites_html.append(suite_html)
        
        return "".join(suites_html)
    
    def generate_markdown(self, output_path: Optional[Path] = None) -> str:
        """Generate Markdown report."""
        timestamp = self.results.get("timestamp", datetime.now().isoformat())
        duration = self.results.get("duration_seconds", 0)
        config = self.results.get("config", {})
        summary = self.results.get("summary", {})
        
        total_passed = summary.get("total_passed", 0)
        total_failed = summary.get("total_failed", 0)
        total_tests = total_passed + total_failed
        pass_rate = (total_passed / total_tests * 100) if total_tests > 0 else 0
        
        md_lines = [
            "# RAG Test Report",
            "",
            f"**Generated:** {timestamp}",
            f"**Duration:** {duration:.1f}s",
            "",
            "## Summary",
            "",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Total Tests | {total_tests} |",
            f"| Passed | {total_passed} |",
            f"| Failed | {total_failed} |",
            f"| Pass Rate | {pass_rate:.0f}% |",
            "",
            "## Configuration",
            "",
            f"- **Workspace:** {config.get('workspace', 'N/A')}",
            f"- **Project:** {config.get('project', 'N/A')}",
            f"- **Branch:** {config.get('branch', 'N/A')}",
            "",
            "## Test Suites",
            ""
        ]
        
        for suite_name, suite_results in self.results.get("suites", {}).items():
            passed = suite_results.get("passed", 0)
            failed = suite_results.get("failed", 0)
            total = passed + failed
            
            status = "✅" if failed == 0 else "⚠️" if passed > 0 else "❌"
            
            md_lines.extend([
                f"### {status} {suite_name.upper()}",
                "",
                f"**Result:** {passed}/{total} passed",
                "",
                "| Test | Status |",
                "|------|--------|"
            ])
            
            for test in suite_results.get("tests", []):
                test_name = test.get("name", "unknown")
                test_passed = test.get("passed", False)
                status_icon = "✅" if test_passed else "❌"
                md_lines.append(f"| {test_name} | {status_icon} |")
            
            md_lines.append("")
        
        md_content = "\n".join(md_lines)
        
        if output_path:
            output_path.write_text(md_content)
        
        return md_content


def main():
    """CLI for report generation."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Generate test reports")
    parser.add_argument("input", type=str, help="Input JSON report file")
    parser.add_argument("--format", choices=["html", "markdown", "both"], default="both")
    parser.add_argument("--output-dir", type=str, help="Output directory")
    args = parser.parse_args()
    
    input_path = Path(args.input)
    output_dir = Path(args.output_dir) if args.output_dir else input_path.parent
    
    generator = ReportGenerator.from_json(input_path)
    
    base_name = input_path.stem
    
    if args.format in ["html", "both"]:
        html_path = output_dir / f"{base_name}.html"
        generator.generate_html(html_path)
        print(f"Generated: {html_path}")
    
    if args.format in ["markdown", "both"]:
        md_path = output_dir / f"{base_name}.md"
        generator.generate_markdown(md_path)
        print(f"Generated: {md_path}")


if __name__ == "__main__":
    main()
