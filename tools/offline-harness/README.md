# CodeCrow offline test harness

This directory is the test-only safety boundary for CodeCrow application and
adapter tests. It blocks live LLM, embedding, VCS, Jira, email, telemetry,
customer, and other external application traffic while allowing deterministic
in-process protocol fakes and isolated PostgreSQL, Redis, and Qdrant fixtures.
Nothing here is linked into production runtime orchestration or production
images.

## Traffic and trust phases

The CI job deliberately separates dependency acquisition from application
tests.

### 1. Authorized build infrastructure

The build phase may use only the origins committed in
[`requirements/build-network-allowlist.txt`](requirements/build-network-allowlist.txt):
PyPI and its artifact host, Maven Central, and Docker Hub's registry and
anonymous-auth endpoints. It records toolchain versions, effective origins,
download reports, resolved package lists, manifests, and digests.

- Python 3.11 dependencies are installed into
  `.llm-handoff-artifacts/p0-03/locked-python311` with `pip --isolated`,
  `--require-hashes`, the exact
  [`requirements/ci-test.lock`](requirements/ci-test.lock), and its committed
  SHA-256 file. The lock is generated from
  [`requirements/ci-test.in`](requirements/ci-test.in); its header contains the
  regeneration command.
- Maven uses a fresh workspace repository and the central-only mirror in
  [`maven/settings-ci.xml`](maven/settings-ci.xml). Cache preparation runs the
  persistence profile's `dependency:go-offline`, then `clean test-compile`
  without executing tests, then explicitly preloads the dynamically selected
  `org.apache.maven.surefire:surefire-junit-platform:3.2.5` through pinned
  `maven-dependency-plugin:3.6.1`, followed by the project's runtime-selected
  `org.junit.platform:junit-platform-launcher:1.10.2`. Only after those steps
  is every regular cache file hashed and validated. The application phase
  starts with `clean`, so build-phase compiled output cannot satisfy the
  offline test.
- Docker uses an empty configuration and home, anonymous credentials, and only
  the three immutable `linux/amd64` references in
  [`requirements/persistence-images-v1.json`](requirements/persistence-images-v1.json).
  Image inspection proves the exact digests and platform before tests begin.

This phase never contacts an LLM, embedding provider, VCS, Jira, customer, or
production application endpoint, and it never executes application tests or
pushes an image.

### 2. Denied application traffic

[`bin/run-offline.sh`](bin/run-offline.sh) runs Python and ordinary Java tests
inside a Bubblewrap namespace with no network namespace access. The runner:

- accepts only the real `/usr/bin/bwrap` and proves namespace creation before
  starting the test;
- uses empty `/run`, `/home`, `/root`, and `HOME`, which also hides Docker,
  Podman, containerd, and agent sockets;
- clears the environment, provider credentials, proxy variables, and user
  package/Maven configuration;
- masks repository `.env`, PEM, and key files and rejects named credential
  symlinks;
- accepts only the locked Python 3.11 runtime or approved system/setup-python
  roots and Java 17; and
- mounts the prepared Maven repository read-only and verifies the Python lock
  and approved Certifi bundle before running locked Python.

Python and Java guards add a second boundary inside the process. An in-process
service receives a lease only for one literal loopback address and one exact,
already-bound port. Hostnames, wildcard addresses, port ranges, and blanket
loopback access are rejected. Leases are reference-counted, removed on close,
and treated as test failures if leaked. Java guard close is linearizable with
lease registration: it atomically rejects new leases, snapshots and clears the
registry, and reports leaked endpoints in sorted order with duplicate counts.
Closing a lease after guard teardown is harmless.

An unexpected DNS, socket, datagram, or process attempt is recorded before I/O
and both guard implementations raise an exception containing that exact,
sanitized `ExternalCall` object. Exception messages are derived only from its
sanitized target. An intentional denial test must acknowledge the same object
and exact boundary, operation, phase, and sanitized target. Teardown fails on
live calls, leaked leases, or any unacknowledged blocked call; catching the
exception alone cannot make the test pass.

The real persistence profile needs the Docker daemon and therefore runs outside
Bubblewrap under `env -i`. Testcontainers starts only the already-attested
images with a never-pull policy, Ryuk and reuse disabled, and a forced literal
`127.0.0.1` host. Once the services are started, JDBC, RESP, and Qdrant clients
receive exact mapped-port leases from the Java network boundary. Docker image
events are captured for the whole application phase and fail validation on any
pull or push.

Build downloads and application calls are reported separately. Registry or
package downloads are never misreported as application calls, and the
application ledger must always report `live_call_count: 0`.

## Shared contracts and redaction

- [`schema/external-call-ledger-v1.schema.json`](schema/external-call-ledger-v1.schema.json)
  defines the canonical Java/Python ledger; the shared golden document is
  [`fixtures/golden/external-call-ledger-v1.json`](fixtures/golden/external-call-ledger-v1.json).
- [`schema/scripted-scenario-v1.schema.json`](schema/scripted-scenario-v1.schema.json)
  defines deterministic response and fault scheduling; the replay fixture is
  [`fixtures/golden/scripted-scenario-v1.json`](fixtures/golden/scripted-scenario-v1.json).
- [`fixtures/golden/target-redaction-v1.json`](fixtures/golden/target-redaction-v1.json)
  is the shared target-redaction corpus.
- [`fixtures/protocol/`](fixtures/protocol/) contains neutral, versioned GitHub,
  GitLab, Bitbucket, Jira, and embedding fixtures with no customer source or
  credentials.

A ledger stores only boundary, operation, outcome, phase, sequence,
simulation/live flags, and a sanitized target. URLs lose user information,
path, query, and fragment. Unknown or malformed targets become
`<redacted-target>`. Payloads, prompts, source, headers, response bodies, and
credentials are never recorded.

## Python API

The reusable package is
`python-ecosystem/test-support/codecrow_test_harness`. The inference and RAG
`pytest.ini` files load its plugin before application construction.

- `NetworkDenyGuard.register_test_service("127.0.0.1", port, boundary)` returns
  an exact endpoint lease.
- `ProcessDenyGuard.register_test_process((absolute_executable, ...))` allows
  only that exact argv.
- `CredentialScrubber` clears provider credentials, supplies deterministic
  service secrets, blocks later credential reintroduction, and verifies the
  environment again at teardown. A small set of explicit synthetic credential
  literals is permitted only ephemerally; leaking one still fails teardown.
- `ScriptedScenario` schedules response, structured, stream, malformed,
  rate-limit, timeout, cancellation, overage, page, duplicate, and retryable
  steps by operation and ordinal.
- `ScriptedLlmFake` supports sync/async invoke and stream, tool binding, and
  structured output.
- `ScriptedEmbeddingFake` and `ContentAddressedEmbeddingFake` expose the current
  query/text/batch ports with explicit model and dimension identity. Blank or
  non-string text and any invalid batch element fail before scenario or ledger
  mutation. An empty batch returns `[]` without consuming a scenario step or
  recording a simulated call.
- `ProtocolFixtureServer`, `FrozenClock`, and `DeterministicIds` provide leased
  local protocol responses and replayable time/identity.

Tests obtain `external_call_ledger`, `network_deny_guard`,
`process_deny_guard`, or the combined `offline_harness` fixture. Production
orchestration never branches on a test flag; adapters receive the same port
shape or protocol endpoint.

## Java API and persistence lifecycle

`java-ecosystem/libs/test-support/.../offline` supplies the matching Java 17
ledger/scenario contract, deterministic clock and IDs, raw-socket and HTTP
network boundary, JUnit extension, cleanup aggregation, and isolated
persistence support. The boundary is explicitly installed per test; Bubblewrap
remains the process-wide backstop for non-persistence suites.

Java boundary close always attempts both guard teardown and restoration of an
owned security manager. If both fail, the lease-leak assertion remains primary
and the cleanup failure is suppressed; active leases are still drained. Direct
boundary use and JUnit-extension teardown enforce the same invariant.

The required `offline-persistence-lifecycle` profile performs two full
generations of PostgreSQL, Redis, and Qdrant:

1. start three containers from exact cached digests;
2. connect through three exact client leases, prove a non-leased literal target
   is blocked and exactly acknowledged, write/read data, reset every store, and
   prove it is clean;
3. stop and remove the first generation;
4. restart three fresh containers and repeat the clean/write/reset checks; and
5. atomically report all six unique full container IDs in deterministic order,
   verify each is absent, write the zero-live-call ledger, and aggregate cleanup
   failures without hiding the primary error.

The outer validator independently re-inspects those exact six IDs, rejects a
retained container, validates that runtime image events contain no pull/push,
and re-inspects the three approved images after the test. The application phase
does run required test-owned containers; it does not pull or push images.

## Authoritative local commands

First prepare the locked Python environment, complete Maven cache, and exact
Docker images using the build-infrastructure steps in
[`.github/workflows/offline-tests.yml`](../../.github/workflows/offline-tests.yml).
Do not substitute an unlocked `.venv` or a partially populated user Maven
cache.

From the repository root, run the shared Python harness with the frozen runner:

```bash
REPO=/var/www/html/codecrow/codecrow-public
PYTHON="$REPO/.llm-handoff-artifacts/p0-03/locked-python311/bin/python"
cd "$REPO"
rm -f .coverage
tools/offline-harness/bin/run-offline.sh "$PYTHON" -m pytest \
  python-ecosystem/test-support/tests -q \
  --ignore=python-ecosystem/test-support/tests/test_offline_runner.py \
  --ignore=python-ecosystem/test-support/tests/p003_production_adapter_contracts \
  --cov=python-ecosystem/test-support/codecrow_test_harness \
  --cov-branch --cov-fail-under=100 \
  --cov-report=json:.llm-handoff-artifacts/p0-03/coverage/python-core.json
```

The runner's own tests execute outside the wrapper because they must launch and
inspect nested wrapper processes:

```bash
"$PYTHON" -m pytest \
  python-ecosystem/test-support/tests/test_offline_runner.py -q
```

Run production Python adapters with explicit plugin loading and their own
ledger:

```bash
cd "$REPO/python-ecosystem/test-support"
CODECROW_EXTERNAL_CALL_LEDGER="$REPO/.llm-handoff-artifacts/p0-03/test-ledgers/python-production-adapters.json" \
  ../../tools/offline-harness/bin/run-offline.sh "$PYTHON" -m pytest \
  tests/p003_production_adapter_contracts -q \
  -p codecrow_test_harness.pytest_plugin
```

Inference unit/integration and RAG unit/integration are separate processes with
separate ledgers, exactly as shown in the workflow. The RAG unit command
deselects only
`tests/test_api_models.py::TestVectorStorageInspectionModels::test_graph_limits_are_bounded`.
That Phase 0 model-bound expectation already fails independently of the offline
profile and remains a replacing-behavior task, not an offline-harness pass.

Java commands set
`CODECROW_MAVEN_REPOSITORY=.llm-handoff-artifacts/p0-03/dependency-cache/maven`,
use [`maven/settings-ci.xml`](maven/settings-ci.xml), Maven `-o`, and the frozen
runner. The test-support coverage build and VCS, Jira, and email adapter suites
write separate ledgers. The persistence profile is the sole exception to the
Bubblewrap invocation: use the workflow's scrubbed `env -i` command and its
post-run ledger, image-event, image-inspection, and exact-container validators.

Validate any resulting ledger or persistence evidence with the committed
tools:

```bash
"$PYTHON" tools/offline-harness/bin/validate-ledgers.py \
  .llm-handoff-artifacts/p0-03/test-ledgers
"$PYTHON" tools/offline-harness/bin/validate-persistence-images.py \
  tools/offline-harness/requirements/persistence-images-v1.json \
  .llm-handoff-artifacts/p0-03/persistence/runtime-image-inspect.json
"$PYTHON" tools/offline-harness/bin/validate-persistence-container-report.py \
  .llm-handoff-artifacts/p0-03/persistence/container-report.json
```

## Current validation and owned limitations

Local locked-runtime evidence is stored under
`.llm-handoff-artifacts/p0-03/`. It includes full Python line/branch coverage,
production-adapter ledgers, separate inference/RAG ledgers, Java Surefire and
JaCoCo results, RED cache-provenance checkpoints, and persistence lifecycle
evidence.

The final local checkpoints on 2026-07-14 were:

| Lane | Result |
|---|---|
| Python shared harness | 125 passed, one intentional profile-only skip; 1,174/1,174 statements and 306/306 branches |
| Python production adapters | 92 passed; ledger 154 calls = 153 simulated plus one exactly acknowledged `PRE_DNS` denial, live 0 |
| Inference | 1,058 unit and 76 integration tests passed; both ledgers live 0 |
| RAG | 819 unit tests passed with the one declared deselection; 93 integration tests passed; both ledgers live 0 |
| Java test support | 47 passed; 2,586/2,586 instructions, 166/166 branches, 548/548 lines, 217/217 complexity, 134/134 methods, and 23/23 classes covered |
| Java adapters | VCS 2, Jira 3, and email 2 tests passed with their expected zero-live ledgers |
| Persistence post-review V6 | Upstream core 1,247, test support 47, and persistence IT 1 passed; support JaCoCo repeated the exact 100% totals above |
| Persistence postconditions | Ledger live 0; six exact IDs absent; one zero-reclaim prune event and zero pulls/pushes; three image digests reattested; 3,500-file Maven cache byte-identical; authorized 67-container baseline unchanged |
| Workflow/static provenance | 19 tests passed; YAML, shell, Python-tool syntax, lock, links, and frozen source digests validated |

The persistence bundle is
`.llm-handoff-artifacts/p0-03/green/persistence-local-post-review-v6`; its
application build-log SHA-256 is
`866b79f75666749fcfcedd534dce9e5fc77957941daef0a02f7c4cbdf56f9d3d`
and its Stage B validation SHA-256 is
`402e09c9f5ba1f089c9f657881e84b12d962535f77de192cea15625a5adf834e`.

The workflow is configured for `ubuntu-24.04`, a 90-minute timeout, read-only
repository permissions, and evidence upload including Surefire and Failsafe
reports. A hosted GitHub Actions run has not been executed from this local
worktree; local execution and static workflow validation must not be presented
as a hosted-run result.

Two observed production behaviors are explicitly typed gaps, not fake parity:

- Ollama can return a short embedding batch without raising the fake's
  cardinality error; `P2-09` owns the production replacement.
- Vertex express-key construction is rejected by the credential-safe offline
  route before I/O; `P3-03` owns the provider-neutral production route.

P0-03 supplies deterministic infrastructure and fixtures for `ING-01`–`ING-03`,
`ING-07`, `REV-06`–`REV-08`, `OUT-03`–`OUT-06`, `JIRA-01`–`JIRA-02`,
`MCP-01`–`MCP-02`, `POL-05`–`POL-06`, `RAG-01`, `RAG-03`, `RAG-05`,
`RAG-07`, `ADM-04`–`ADM-05`, and `CCH-03`. Product tasks that consume the
harness still own correctness for each row; a fake passing its own contract
does not certify production behavior by itself.
