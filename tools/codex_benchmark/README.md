# Codex subscription benchmark

This adapter is for an explicitly enabled benchmark, using the existing host
`codex login` ChatGPT subscription and exact `gpt-6-luna` model. It never reads or
copies OAuth credentials. Production review stages and the normal entrypoint are
unchanged. Python dependencies come from the inference CI environment.

Build application images with `deployment/build/production-build.sh --build-only`.
Keep the model workspace empty and separate from the application and gold labels.
Generate a private random local bridge token; it is not an API key. Launch:

```bash
python -m tools.codex_benchmark.bridge \
  --artifacts "$RUN/model" --workspace "$RUN/model-workspace" \
  --token-file "$RUN/config/bridge.token" --host 172.17.0.1 --capacity 4
```

Use the actual Docker host address, bound only to a local Docker interface. The
CLI must already report ChatGPT authentication and list `gpt-6-luna`. The bridge
checks both before serving requests. It records request hashes, model, auth type,
tool names, native usage when available, and errors in `subscription-ledger.jsonl`.
No model API keys are passed to the CLI.

Prepare private `RUN/config/{inference-orchestrator,rag-pipeline}.env` files from
the current service settings, removing model credentials and remote model routes.
Use a separate random service secret in both, fresh Redis at
`redis://redis:6379/1`, and `NEW_RELIC_ENABLED=false`. Set the inference variables:

- `CODECROW_CODEX_BENCHMARK=1`
- `CODECROW_SUBSCRIPTION_BRIDGE_URL=http://host.docker.internal:18771/v1`
- `CODECROW_SUBSCRIPTION_BRIDGE_TOKEN` to the local bridge token
- `ALLOW_PRIVATE_ENDPOINTS=true`
- `RAG_API_URL=http://rag-pipeline:8001`

Set RAG `STRUCTURAL_INDEX_ROOT=/var/lib/codecrow/structural-index` and
`UVICORN_WORKERS=1`. Start the dedicated services:

```bash
CODECROW_BENCHMARK_RUN="$RUN" docker compose \
  -f tools/codex_benchmark/compose.yml up -d --no-build --wait
```

Use the matching restored harness with `--ai-provider openai_compatible`, model
`gpt-6-luna`, local bridge URL/token, `--use-mcp-tools`, RAG port 19004, inference
port 19015, and the exact dedicated container names from Compose. Keep the
selected corpus and work/output directories separate from previous runs. The
benchmark entrypoint rejects all other model routes before provider construction;
the dedicated inference container also resolves API/OpenRouter hosts to loopback.
GitHub source acquisition still requires its ordinary GitHub token.

For extraction, deduplication and judging set `MARTIAN_BASE_URL` to the same
bridge (host clients use its Docker-interface address), `MARTIAN_API_KEY` to the
local bridge token, `MARTIAN_MODEL=gpt-6-luna`, and `MARTIAN_PROVIDER=`. No scoring
stage should retain its API-provider defaults. Preserve completed artifacts when
resuming; do not clear a live run's index volume.

Native function calls retain their pending Codex turn until CodeCrow supplies
the tool results. Changed history/tool inventory is recorded and reconstructed
from supplied messages. JSON schemas and function-based structured responses are
supported. Built-in shell, browsing, apps and delegation are disabled. Errors
have no remote API fallback. Disconnection cancels the owned request; abandoned
tool waits are reclaimed after ten minutes.

Temperature, top-p and output-token caps have no equivalent in this app-server
protocol. They are recorded as unmapped parameters, never silently claimed as
applied. Codex instruction/context overhead and subscription limits also make
this a distinct transport experiment, not an identical API run. Missing usage
is unknown. Unit and transport probes do not establish benchmark accuracy.

Tests: `python -m pytest tools/codex_benchmark/tests -q`.

For the prepared restored 25-PR corpus, copy the matching harness and scoring
checkout under `RUN/harness` and `RUN/scorer`, link `RUN/codecrow-public` to the
application for contract validation, freeze five golden entries per repository,
and save the GitHub credential in private `RUN/config/github.token`. The prepared
runner uses five review/index workers and four model calls at a time:

```bash
python -m tools.codex_benchmark.run --run-dir "$RUN"
```

It writes `status.json`, logs and commands, then runs review, extraction,
deduplication, judging and dashboard generation in order. On a failed stage,
inspect the saved error before resuming with `--resume`; `--score-only --resume`
reuses saved reviews and completed scoring. Its status reports subprocess
completion; verify all 25 review and evaluation records before reporting metrics.
Service `.env` mounts must be readable by the application UID while their host
parent directory remains private. CLI OAuth files are never mounted.
