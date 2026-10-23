# Codex subscription benchmark

This adapter is for an explicitly enabled benchmark, using the existing host
`codex login` ChatGPT subscription and one explicitly selected model. Set
`CODECROW_CODEX_BENCHMARK_MODEL=gpt-6-sol` for Sol (`gpt-6-luna` is the default).
The bridge, benchmark entrypoint and runner must use the same selection. It never reads or
copies OAuth credentials. Production review stages and the normal entrypoint are
unchanged. Python dependencies come from the inference CI environment.

Build application images with `deployment/build/production-build.sh --build-only`.
Keep the model workspace empty and separate from the application and gold labels.
Generate a private random local bridge token; it is not an API key. Launch:

```bash
CODECROW_CODEX_BENCHMARK_MODEL=gpt-6-sol python -m tools.codex_benchmark.bridge \
  --artifacts "$RUN/model" --workspace "$RUN/model-workspace" \
  --token-file "$RUN/config/bridge.token" --host 172.17.0.1 --capacity 64
```

Use the actual Docker host address, bound only to a local Docker interface. The
CLI must already report ChatGPT authentication and list the selected model. The bridge
checks both before serving requests. It records request hashes, model, auth type,
tool names, native usage when available, and errors in `subscription-ledger.jsonl`.
No model API keys are passed to the CLI.

Prepare private `RUN/config/{inference-orchestrator,rag-pipeline}.env` files from
the current service settings, removing model credentials and remote model routes.
Use a separate random service secret in both, fresh Redis at
`redis://redis:6379/1`, and `NEW_RELIC_ENABLED=false`. Set the inference variables:

- `CODECROW_CODEX_BENCHMARK=1`
- `CODECROW_CODEX_BENCHMARK_MODEL=gpt-6-sol`
- `CODECROW_SUBSCRIPTION_BRIDGE_URL=http://host.docker.internal:18771/v1`
- `CODECROW_SUBSCRIPTION_BRIDGE_TOKEN` to the local bridge token
- `ALLOW_PRIVATE_ENDPOINTS=true`
- `RAG_API_URL=http://rag-pipeline:8001`
- `MAX_CONCURRENT_REVIEWS=16`
- `MAX_CONCURRENT_REVIEW_CALLS=64`
- `REVIEW_STAGE1_MAX_PARALLEL=8`

Set RAG `STRUCTURAL_INDEX_ROOT=/var/lib/codecrow/structural-index` and
`UVICORN_WORKERS=1`, and `RAG_FULL_INDEX_CONCURRENCY=16`. The one API process
owns sixteen spawned build slots. Start the dedicated services:

```bash
CODECROW_BENCHMARK_RUN="$RUN" docker compose \
  -f tools/codex_benchmark/compose.yml up -d --no-build --wait
```

Use the matching restored harness with `--ai-provider openai_compatible`, model
`gpt-6-sol`, local bridge URL/token, `--use-mcp-tools`, RAG port 19004, inference
port 19015, and the exact dedicated container names from Compose. Keep the
selected corpus and work/output directories separate from previous runs. The
benchmark entrypoint rejects all other model routes before provider construction;
the dedicated inference container also resolves API/OpenRouter hosts to loopback.
GitHub source acquisition still requires its ordinary GitHub token.

For extraction, deduplication and judging set `MARTIAN_BASE_URL` to the same
bridge (host clients use its Docker-interface address), `MARTIAN_API_KEY` to the
local bridge token, `MARTIAN_MODEL=gpt-6-sol`, and `MARTIAN_PROVIDER=`. No scoring
stage should retain its API-provider defaults. Preserve completed artifacts when
resuming; do not clear a live run's index volume.

The bridge exposes the default `functions` namespace directly through the
supported `features.code_mode.enabled=false` and
`features.code_mode.direct_only_tool_namespaces=["functions"]` configuration.
This applies even to a `code_mode_only` model. It changes the model's native tool
surface, despite unchanged HTTP function schemas; it does not establish behavior
or cost parity with earlier code-mode runs. Native helper tools may still exist,
so verify the actual call events rather than inferring direct execution from
configuration alone.

Ordinary native function calls retain their pending Codex turn until CodeCrow supplies
the tool results. Changed history/tool inventory is recorded and reconstructed
from supplied messages. JSON schemas and function-based structured responses are
supported. Built-in shell, browsing, apps and delegation are disabled. Errors
have no remote API fallback. Disconnection cancels the owned request. The recursive
agent and verification loops also release their native conversations on return,
error or cancellation between model calls, including during repository reads.
The entrypoint's optional provider scope attaches an opaque `codecrow_owner` in
transport metadata, outside model instructions. Authenticated
`POST /v1/conversations/release` closes that owner before cancelling its active
requests and releasing pending or concurrently created conversations; other owners
and shared clients are unaffected. Cleanup is bounded and fails open with a
diagnostic. Abandoned tool waits still have the ten-minute reaper fallback.

The benchmark-only `codecrow_terminal_tools` request field lists output-schema
function names. The benchmark entrypoint tags direct structured bindings; agent
middleware tags its ToolStrategy output schema only in benchmark mode. Every
assembled tool batch, including ordinary and mixed calls, first checks the exact
current thread/turn/call-to-response mappings. A batch containing only named
output calls then releases its native conversation; ordinary and mixed batches
remain pending for their real tool results. An already recorded match needs no wait.
Otherwise, receipt collection can wait at most
`min(1 second, max(0, SDK read allowance - elapsed request time - 20 seconds))`;
the 20 seconds reserve the existing interrupt and unsubscribe cleanup allowances.
Missing or invalid SDK read-timeout metadata gives no additional wait. A missing
match, timeout or collector failure records a diagnostic and preserves the
answer; it adds no model call or tool result. External cancellation still
propagates and performs owned cleanup. Without this field, even a forced
function call remains pending for its real tool result. Initial required tools
keep the full inventory; first-tool enforcement does not require rebuilding a
native conversation. Disabled evidence tools remain advertised and return an
unresolved diagnostic without another backend read.

The ledger records actual tool choice, serialized request/message/tool/result
sizes, reconstruction reasons and exact `rawResponse/completed` receipts with
response/thread/turn identity and nullable native usage. It also retains native
usage updates (`total` and `last`), terminal snapshots, owner identity and the
latest usage sequence, including late receipts. SDK response usage still uses
cumulative deltas and can remain null even when an exact raw receipt exists.
For accounting, deduplicate raw receipts by response ID only when identity and
usage agree; reconcile their sum with cumulative snapshots. Never add snapshots,
`last` usage or SDK deltas to the raw total. Null, conflicting or absent receipts
remain unknown, and known totals remain lower bounds until generation coverage
is established. An idle facade, interrupt acknowledgement, or pending code-mode
child does not prove that no additional generation occurred. Include abandoned
work and require evidence for the actual direct-call/terminal lifecycle; do not
retroactively certify interrupted child calls from the presence of one receipt.

Two isolated LOW `gpt-5.6-sol` protocol probes recorded a direct terminal call
with 12,400 complete native tokens and a 12.586 ms exact-ID wait (SDK usage was
null), and an ordinary tool continuation with two HTTP requests in one native
thread/turn, natural completion and 12,270 complete native tokens. Their source
and lifecycle proofs are under
`reports/benchmark-quality-20261011/terminal-schema-accounting/` in the workspace.
These examples validate those exercised paths, not review quality, latency or
token savings, and do not repair historical missing accounting.

Temperature, top-p and output-token caps have no equivalent in this app-server
protocol. They are recorded as unmapped parameters, never silently claimed as
applied. Codex instruction/context overhead and subscription limits also make
this a distinct transport experiment, not an identical API run. Missing usage
is unknown. Unit and transport probes do not establish benchmark accuracy.

Tests: `python -m pytest tools/codex_benchmark/tests -q`.

For the prepared restored 10-PR corpus, copy the matching harness and scoring
checkout under `RUN/harness` and `RUN/scorer`, link `RUN/codecrow-public` to the
application for contract validation, freeze two golden entries per repository,
and save the GitHub credential in private `RUN/config/github.token`. The prepared
runner defaults to sixteen review/index workers and thirty-two scoring calls
per batch. Bridge capacity defaults to sixty-four and is exposed at `/health`:

```bash
CODECROW_CODEX_BENCHMARK_MODEL=gpt-6-sol python -m tools.codex_benchmark.run \
  --run-dir "$RUN" --limit 10 --tool-name CodeCrow-CodexSol-10PR
```

It writes `status.json`, logs and commands, then runs review, extraction,
deduplication, judging and dashboard generation in order. On a failed stage,
inspect the saved error before resuming with `--resume`; `--score-only --resume`
reuses saved reviews and completed scoring. Its status reports subprocess
completion; verify all selected review and evaluation records before reporting metrics.
Service `.env` mounts must be readable by the application UID while their host
parent directory remains private. CLI OAuth files are never mounted.


For the full Qodo corpus, keep the supplied 100-row JSONL unchanged in the run
directory and use an isolated scorer output. Empty Martian golden-comment files
are not a substitute for this dataset flag. For a Luna 5.6 run, set
`CODECROW_CODEX_BENCHMARK_MODEL=gpt-5.6-luna` consistently in the bridge,
inference service and runner, then run:

```bash
CODECROW_CODEX_BENCHMARK_MODEL=gpt-5.6-luna python -m tools.codex_benchmark.run \
  --run-dir "$RUN" --dataset-file "$RUN/qodo100.jsonl" --limit 100 \
  --tool-name CodeCrow-Qodo100-CodexLuna56 --jobs 16 --index-jobs 16 \
  --score-batch-size 32
```

The same dataset path is passed to review and scoring. The restored harness uses
Qodo's reference issues, checks complete extraction and judging, and builds a
Qodo dashboard. Report Qodo metrics across all `qodo_issue` references; Martian's
strict/core category profiles exclude those references and are not valid Qodo
scores. Model proxy capacity is independently controlled by bridge `--capacity`.
