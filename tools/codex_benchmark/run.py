"""Run a prepared, isolated corpus through the subscription bridge and scorer."""
from __future__ import annotations
import argparse
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys

from .rpc import MODEL


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--limit', type=int, required=True)
    parser.add_argument('--tool-name', required=True)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--score-only', action='store_true')
    args = parser.parse_args()
    run = args.run_dir.resolve()
    os.umask(0o077)
    env = {k: v for k, v in os.environ.items() if k not in {
        'OPENROUTER_API_KEY', 'OPENAI_API_KEY', 'CODEX_API_KEY', 'ANTHROPIC_API_KEY',
        'GOOGLE_API_KEY', 'GEMINI_API_KEY', 'OPENAI_BASE_URL', 'MARTIAN_PROVIDER',
        'CODECROW_AI_BASE_URL', 'CODECROW_AI_CUSTOM_PARAMETERS',
    }}
    token = (run/'config/bridge.token').read_text().strip()
    env.update({
        'GH_TOKEN': (run/'config/github.token').read_text().strip(),
        'CODECROW_SERVICE_SECRET': (run/'config/service.token').read_text().strip(),
        'CODECROW_RAG_URL': 'http://127.0.0.1:19004',
        'CODECROW_INFERENCE_URL': 'http://127.0.0.1:19015',
        'CODECROW_RAG_CONTAINER': 'codecrow-codex-benchmark-rag-pipeline',
        'CODECROW_AGENTIC_CONTAINER': 'codecrow-codex-benchmark-inference-orchestrator',
        'CODECROW_AI_PROVIDER': 'openai_compatible', 'CODECROW_AI_MODEL': MODEL,
        'CODECROW_AI_API_KEY': token,
        'CODECROW_AI_BASE_URL': 'http://host.docker.internal:18771/v1',
        'MARTIAN_API_KEY': token, 'MARTIAN_BASE_URL': 'http://172.17.0.1:18771/v1',
        'MARTIAN_MODEL': MODEL, 'MARTIAN_PROVIDER': '',
        'CRB_EXTRACT_BATCH_SIZE': '4', 'CRB_DEDUP_BATCH_SIZE': '4',
        'CRB_JUDGE_BATCH_SIZE': '4', 'CRB_LLM_CALL_TIMEOUT': '300',
        'CRB_REVIEW_TIMEOUT': '10800', 'CRB_MAX_RETRIES': '8',
        'CRB_RATE_LIMIT_SLEEP': '75',
        'CRB_LLM_LEDGER_PATH': str(run/'scoring-llm-ledger.jsonl'),
        'UV_NO_SYNC': '1', 'PYTHONUNBUFFERED': '1', 'TMPDIR': str(run/'work/tmp'),
    })
    common = [sys.executable, str(run/'harness/codecrow_crb_harness.py')]
    review = common + ['full', '--review-approach', 'classic', '--use-mcp-tools',
        '--rag-source', 'benchmark', '--rag-index-scope', 'repository',
        '--benchmark-dir', str(run/'scorer'), '--work-dir', str(run/'work'),
        '--output', str(run/'benchmark_data.json'), '--workspace', 'code-review-benchmark',
        '--tool-name', args.tool_name, '--limit', str(args.limit), '--jobs', '5', '--index-jobs', '5',
        '--review-timeout', '10800', '--index-timeout', '7200', '--skip-existing-index',
        '--rag-url', env['CODECROW_RAG_URL'], '--inference-url', env['CODECROW_INFERENCE_URL'],
        '--rag-filesystem-mode', 'docker', '--rag-container', env['CODECROW_RAG_CONTAINER'],
        '--rag-workspace-root', '/tmp', '--agentic-workspace-mode', 'docker',
        '--agentic-container', env['CODECROW_AGENTIC_CONTAINER'],
        '--agentic-workspace-root', '/tmp/codecrow-agentic-codex-subscription',
        '--index-capacity-lock', str(run/'work/index-capacity.lock'),
        '--ai-provider', 'openai_compatible', '--ai-model', MODEL,
        '--ai-base-url', env['CODECROW_AI_BASE_URL'], '--ai-custom-parameters', '{}']
    score = common + ['score', '--benchmark-dir', str(run/'scorer'),
        '--work-dir', str(run/'work'), '--output', str(run/'benchmark_data.json'),
        '--tool-name', args.tool_name, '--score-run-name', run.name, '--skip-uv-sync']
    if args.resume:
        review.append('--resume'); score.append('--reuse-score')
    steps = [('review', review), ('scoring', score)]
    if args.score_only: steps = steps[1:]
    (run/'provenance/commands.json').write_text(json.dumps(dict(steps), indent=2)+'\n')
    def status(**values):
        values.update(updated_utc=datetime.datetime.now(datetime.UTC).isoformat(), pid=os.getpid())
        temp=run/'status.tmp';temp.write_text(json.dumps(values,indent=2)+'\n');temp.replace(run/'status.json')
    for stage, command in steps:
        with (run/'logs'/f'{stage}.log').open('a') as log:
            process=subprocess.Popen(command,cwd=run,env=env,stdout=log,stderr=subprocess.STDOUT)
            status(state='running',stage=stage,child_pid=process.pid)
            code=process.wait()
        if code:
            status(state='failed',stage=stage,exit_code=code)
            return code
    status(state='completed',stage='done',exit_code=0)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
