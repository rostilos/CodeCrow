from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from .collect import discover, link_discovery_selection, materialize
from .config import load_config
from .corpus import validate_corpus
from .current_comments import attest_current_comments
from .curation import (
    archive_draft_sources,
    export_curation_packet,
    hydrate_review_threads,
    release_selection,
    validate_draft_file,
)
from .execution_corpus import create_execution_corpus
from .github import GitHubClient
from .judge import judge_run
from .metrics import build_metrics
from .postfix import (
    apply_post_fix_plan,
    build_post_fix_control,
    create_post_fix_plan,
    create_post_fix_control_set,
    judge_post_fix_run,
    run_post_fix_analysis,
    verify_post_fix_replay,
)
from .preflight import operator_preflight
from .protocol import (
    build_reproducibility_package,
    create_judge_evaluation,
    create_seal_ledger,
    create_study_registration,
    export_judge_evaluation_packet,
    verify_reproducibility_package,
)
from .replay import apply_plan, create_plan, verify_replay
from .repository_evidence import create_repository_evidence
from .runner import run_analysis
from .util import read_json


def _path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="magento2-benchmark",
        description=(
            "Collect, replay, run, judge, and report CodeCrow's frozen Magento 2 "
            "review benchmark."
        ),
    )
    parser.add_argument(
        "--config",
        type=_path,
        help="TOML configuration (defaults are used when omitted)",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser(
        "validate",
        help="validate corpus invariants without network access",
    )
    validate.add_argument("--corpus", type=_path, required=True)
    validate.add_argument("--paper-ready", action="store_true")
    validate.add_argument("--required-cases", type=int)

    validate_draft = commands.add_parser(
        "validate-draft",
        help="validate the checked-in provisional 50-case source corpus",
    )
    validate_draft.add_argument("--draft", type=_path, required=True)

    current_comments = commands.add_parser(
        "verify-current-comments",
        help=(
            "seal the current complete REST comment lists for every selected "
            "draft root"
        ),
    )
    current_comments.add_argument("--draft", type=_path, required=True)
    current_comments.add_argument("--output", type=_path, required=True)
    current_comments.add_argument("--offline", action="store_true")

    discovery = commands.add_parser(
        "discover",
        help="cache candidate root review comments from GitHub",
    )
    discovery.add_argument("--pages", type=int, default=10)
    discovery.add_argument("--output", type=_path, required=True)
    discovery.add_argument("--offline", action="store_true")

    linkage = commands.add_parser(
        "link-discovery-selection",
        help="bind an ordered draft or released selection to a sealed discovery pool",
    )
    linkage.add_argument("--discovery", type=_path, required=True)
    linkage.add_argument("--selection", type=_path, required=True)
    linkage.add_argument("--output", type=_path, required=True)

    collect = commands.add_parser(
        "materialize",
        help="turn an adjudicated 50-case selection into a frozen corpus",
    )
    collect.add_argument("--selection", type=_path, required=True)
    collect.add_argument("--repository-path", type=_path, required=True)
    collect.add_argument("--output", type=_path, required=True)
    collect.add_argument("--required-cases", type=int)
    collect.add_argument("--offline", action="store_true")

    packet = commands.add_parser(
        "curation-packet",
        help="export frozen code/fix evidence for human or blinded LLM curation",
    )
    packet.add_argument("--draft", type=_path, required=True)
    packet.add_argument("--repository-path", type=_path, required=True)
    packet.add_argument("--output", type=_path, required=True)
    packet.add_argument("--thread-evidence", type=_path)

    threads = commands.add_parser(
        "collect-threads",
        help=(
            "fetch GraphQL thread resolution plus REST comments/reviews "
            "(token required for paper evidence)"
        ),
    )
    threads.add_argument("--draft", type=_path, required=True)
    threads.add_argument("--output", type=_path, required=True)
    threads.add_argument("--offline", action="store_true")

    source_archive = commands.add_parser(
        "archive-draft-sources",
        help="archive and verify exact REST inputs for the pinned draft",
    )
    source_archive.add_argument("--draft", type=_path, required=True)
    source_archive.add_argument("--output", type=_path, required=True)
    source_archive.add_argument("--offline", action="store_true")

    release = commands.add_parser(
        "release-selection",
        help="apply explicit curation decisions to the 50-case draft",
    )
    release.add_argument("--draft", type=_path, required=True)
    release.add_argument("--decisions", type=_path, required=True)
    release.add_argument("--source-archive", type=_path)
    release.add_argument("--thread-evidence", type=_path)
    release.add_argument("--curation-packet", type=_path)
    release.add_argument("--output", type=_path, required=True)
    release.add_argument("--paper-ready", action="store_true")

    execution_corpus = commands.add_parser(
        "execution-corpus",
        help=(
            "project a strict released corpus into the label-free pre-unseal "
            "analysis fixture"
        ),
    )
    execution_corpus.add_argument("--corpus", type=_path, required=True)
    execution_corpus.add_argument("--output", type=_path, required=True)

    repository_evidence = commands.add_parser(
        "repository-evidence",
        help=(
            "materialize a sanitized bare Git source store for offline "
            "B/H/F/fix reconstruction"
        ),
    )
    repository_evidence.add_argument("--corpus", type=_path, required=True)
    repository_evidence.add_argument(
        "--source-repository",
        type=_path,
        required=True,
    )
    repository_evidence.add_argument(
        "--output-root",
        type=_path,
        required=True,
    )

    replay_plan = commands.add_parser(
        "replay-plan",
        help="create a read-only plan for fork refs and pull requests",
    )
    replay_plan.add_argument(
        "--execution-corpus",
        type=_path,
        required=True,
    )
    replay_plan.add_argument("--fork", required=True, metavar="OWNER/REPOSITORY")
    replay_plan.add_argument("--output", type=_path, required=True)

    replay_apply = commands.add_parser(
        "replay-apply",
        help="apply an approved replay plan to the exact fork",
    )
    replay_apply.add_argument("--plan", type=_path, required=True)
    replay_apply.add_argument("--output", type=_path, required=True)
    replay_apply.add_argument(
        "--confirm-fork",
        required=True,
        metavar="OWNER/REPOSITORY",
    )
    replay_apply.add_argument("--source-repository", type=_path)
    replay_apply.add_argument("--git-remote")

    replay_verify = commands.add_parser(
        "verify-replay",
        help="live-check every fork ref/PR and seal a replay attestation",
    )
    replay_verify.add_argument(
        "--execution-corpus",
        type=_path,
        required=True,
    )
    replay_verify.add_argument("--replay-lock", type=_path, required=True)
    replay_verify.add_argument("--output", type=_path, required=True)

    operator = commands.add_parser(
        "operator-preflight",
        help="run a strictly read-only paper-run readiness audit",
    )
    operator.add_argument(
        "--execution-corpus",
        type=_path,
        required=True,
    )
    operator.add_argument("--replay-lock", type=_path, required=True)
    operator.add_argument("--replay-attestation", type=_path)
    operator.add_argument("--repository-path", type=_path, required=True)
    operator.add_argument("--output", type=_path, required=True)

    post_fix_plan = commands.add_parser(
        "post-fix-replay-plan",
        help="reconstruct and bind exact B-to-verified-F control snapshots",
    )
    post_fix_plan.add_argument("--corpus", type=_path, required=True)
    post_fix_plan.add_argument(
        "--primary-replay-lock",
        type=_path,
        required=True,
    )
    post_fix_plan.add_argument(
        "--repository-path",
        type=_path,
        required=True,
    )
    post_fix_plan.add_argument("--output", type=_path, required=True)

    post_fix_apply = commands.add_parser(
        "post-fix-replay-apply",
        help="create immutable separate B-to-F refs and PRs in the exact fork",
    )
    post_fix_apply.add_argument(
        "--execution-corpus",
        type=_path,
        required=True,
    )
    post_fix_apply.add_argument(
        "--primary-replay-lock",
        type=_path,
        required=True,
    )
    post_fix_apply.add_argument("--plan", type=_path, required=True)
    post_fix_apply.add_argument("--output", type=_path, required=True)
    post_fix_apply.add_argument(
        "--confirm-fork",
        required=True,
        metavar="OWNER/REPOSITORY",
    )
    post_fix_apply.add_argument("--source-repository", type=_path)
    post_fix_apply.add_argument("--git-remote")

    post_fix_verify = commands.add_parser(
        "verify-post-fix-replay",
        help="live-check and attest every immutable B-to-F ref and PR",
    )
    post_fix_verify.add_argument(
        "--execution-corpus",
        type=_path,
        required=True,
    )
    post_fix_verify.add_argument(
        "--primary-replay-lock",
        type=_path,
        required=True,
    )
    post_fix_verify.add_argument(
        "--post-fix-replay-lock",
        type=_path,
        required=True,
    )
    post_fix_verify.add_argument("--output", type=_path, required=True)

    analysis = commands.add_parser(
        "run",
        help="run CodeCrow against replay PRs using an existing exact base index",
    )
    analysis.add_argument(
        "--execution-corpus",
        type=_path,
        required=True,
    )
    analysis.add_argument("--replay-lock", type=_path, required=True)
    analysis.add_argument("--replay-attestation", type=_path)
    analysis.add_argument("--repository-path", type=_path, required=True)
    analysis.add_argument("--output-dir", type=_path, required=True)
    analysis.add_argument(
        "--run-id",
        help=(
            "safe preregistered analysis run ID; generated automatically "
            "when omitted"
        ),
    )
    analysis.add_argument("--analysis-model")
    analysis.add_argument(
        "--expected-analysis-response-model",
        help=(
            "exact provider-resolved analysis model; defaults to "
            "--analysis-model when that override is used"
        ),
    )
    analysis.add_argument(
        "--case",
        dest="case_ids",
        action="append",
        help="case ID to run (repeatable)",
    )
    analysis.add_argument("--limit", type=int)
    analysis.add_argument("--resume", action="store_true")

    post_fix_analysis = commands.add_parser(
        "post-fix-run",
        help=(
            "run preregistered verified-F controls with the paired H "
            "configuration, B index, and runtime"
        ),
    )
    post_fix_analysis.add_argument(
        "--execution-corpus",
        type=_path,
        required=True,
        help="label-free pre-unseal analysis execution corpus",
    )
    post_fix_analysis.add_argument("--registration", type=_path, required=True)
    post_fix_analysis.add_argument(
        "--primary-replay-lock",
        type=_path,
        required=True,
    )
    post_fix_analysis.add_argument(
        "--post-fix-replay-lock",
        type=_path,
        required=True,
    )
    post_fix_analysis.add_argument(
        "--post-fix-replay-attestation",
        type=_path,
        required=True,
    )
    post_fix_analysis.add_argument(
        "--primary-run",
        type=_path,
        required=True,
    )
    post_fix_analysis.add_argument(
        "--repository-path",
        type=_path,
        required=True,
    )
    post_fix_analysis.add_argument("--output-dir", type=_path, required=True)
    post_fix_analysis.add_argument("--resume", action="store_true")

    judge = commands.add_parser(
        "judge",
        help="blindly judge every gold/candidate pair and unmatched finding",
    )
    judge.add_argument("--corpus", type=_path, required=True)
    judge.add_argument("--run", dest="run_path", type=_path, required=True)
    judge.add_argument("--repository-path", type=_path, required=True)
    judge.add_argument("--output-dir", type=_path, required=True)
    judge.add_argument(
        "--judgment-id",
        help=(
            "safe preregistered judgment ID; generated automatically when "
            "omitted"
        ),
    )

    post_fix_judge = commands.add_parser(
        "post-fix-judge",
        help=(
            "judge conditional disappearance of H true positives at "
            "verified F after unseal"
        ),
    )
    post_fix_judge.add_argument("--corpus", type=_path, required=True)
    post_fix_judge.add_argument("--registration", type=_path, required=True)
    post_fix_judge.add_argument("--seal-ledger", type=_path, required=True)
    post_fix_judge.add_argument("--primary-run", type=_path, required=True)
    post_fix_judge.add_argument(
        "--primary-judgment",
        type=_path,
        required=True,
    )
    post_fix_judge.add_argument(
        "--post-fix-run",
        type=_path,
        required=True,
    )
    post_fix_judge.add_argument(
        "--primary-replay-lock",
        type=_path,
        required=True,
    )
    post_fix_judge.add_argument(
        "--post-fix-replay-lock",
        type=_path,
        required=True,
    )
    post_fix_judge.add_argument(
        "--post-fix-replay-attestation",
        type=_path,
        required=True,
    )
    post_fix_judge.add_argument(
        "--registered-analysis-run",
        dest="registered_analysis_runs",
        type=_path,
        action="append",
        required=True,
    )
    post_fix_judge.add_argument(
        "--registered-post-fix-analysis-run",
        dest="registered_post_fix_analysis_runs",
        type=_path,
        action="append",
        required=True,
    )
    post_fix_judge.add_argument(
        "--repository-path",
        type=_path,
        required=True,
    )
    post_fix_judge.add_argument("--output-dir", type=_path, required=True)

    post_fix_control = commands.add_parser(
        "post-fix-control",
        help="build the bound conditional H-TP disappearance control artifact",
    )
    post_fix_control.add_argument("--corpus", type=_path, required=True)
    post_fix_control.add_argument("--registration", type=_path, required=True)
    post_fix_control.add_argument("--seal-ledger", type=_path, required=True)
    post_fix_control.add_argument(
        "--primary-replay-lock",
        type=_path,
        required=True,
    )
    post_fix_control.add_argument("--primary-run", type=_path, required=True)
    post_fix_control.add_argument(
        "--primary-judgment",
        type=_path,
        required=True,
    )
    post_fix_control.add_argument(
        "--post-fix-run",
        type=_path,
        required=True,
    )
    post_fix_control.add_argument(
        "--post-fix-replay-lock",
        type=_path,
        required=True,
    )
    post_fix_control.add_argument(
        "--post-fix-replay-attestation",
        type=_path,
        required=True,
    )
    post_fix_control.add_argument(
        "--post-fix-judgment",
        type=_path,
        required=True,
    )
    post_fix_control.add_argument(
        "--repository-path",
        type=_path,
        required=True,
    )
    post_fix_control.add_argument("--output", type=_path, required=True)

    post_fix_control_set = commands.add_parser(
        "post-fix-control-set",
        help="semantically validate and bind every registered post-fix control",
    )
    post_fix_control_set.add_argument("--manifest", type=_path, required=True)
    post_fix_control_set.add_argument("--output", type=_path, required=True)
    judge.add_argument("--judge-model")
    judge.add_argument(
        "--expected-response-model",
        help=(
            "exact provider-resolved judge model; defaults to --judge-model "
            "when that override is used"
        ),
    )

    metrics = commands.add_parser(
        "metrics",
        help="compute reference-set metrics and paired model comparisons",
    )
    metrics.add_argument("--corpus", type=_path, required=True)
    metrics.add_argument(
        "--judgment",
        dest="judgments",
        type=_path,
        required=True,
        action="append",
        help="judgments.json path (repeat for model comparisons)",
    )
    metrics.add_argument(
        "--analysis-run",
        dest="analysis_runs",
        type=_path,
        action="append",
        help=(
            "bound analysis run.json path (repeat for every judgment; "
            "required for paper-ready metrics)"
        ),
    )
    metrics.add_argument(
        "--post-fix-analysis-run",
        dest="post_fix_analysis_runs",
        type=_path,
        action="append",
        help="registered verified-F analysis run (repeat for every H run)",
    )
    metrics.add_argument(
        "--post-fix-control-set",
        type=_path,
        help=(
            "exact control-set artifact; requires --post-fix-artifact raw "
            "semantic evidence"
        ),
    )
    metrics.add_argument(
        "--post-fix-artifact",
        dest="post_fix_artifacts",
        type=_path,
        action="append",
        help=(
            "raw verified-F replay/run/judgment/control file or directory "
            "(repeat as needed)"
        ),
    )
    metrics.add_argument(
        "--replay-lock",
        dest="replay_locks",
        type=_path,
        action="append",
        help=(
            "optional external replay lock (run-relative copies are "
            "authoritative for paper-ready metrics)"
        ),
    )
    metrics.add_argument(
        "--replay-attestation",
        dest="replay_attestations",
        type=_path,
        action="append",
        help=(
            "optional external live replay attestation (repeat as needed)"
        ),
    )
    metrics.add_argument(
        "--repository-path",
        type=_path,
        help=(
            "local frozen Magento 2 Git clone used to independently "
            "reconstruct judge prompt evidence (required for paper-ready "
            "metrics when candidate findings exist)"
        ),
    )
    metrics.add_argument(
        "--repository-evidence",
        type=_path,
        help=(
            "immutable source-repository evidence manifest; required for "
            "artifact-integrity-ready metrics"
        ),
    )
    metrics.add_argument("--study-registration", type=_path)
    metrics.add_argument("--seal-ledger", type=_path)
    metrics.add_argument("--judge-evaluation", type=_path)
    metrics.add_argument("--output", type=_path, required=True)
    metrics.add_argument("--bootstrap-iterations", type=int, default=10_000)
    metrics.add_argument("--seed", type=int, default=20_260_729)

    dashboard = commands.add_parser(
        "dashboard",
        help="build a zero-dependency static dashboard from metrics JSON",
    )
    dashboard.add_argument("--metrics", type=_path, required=True)
    dashboard.add_argument("--output-dir", type=_path, required=True)

    register_study = commands.add_parser(
        "register-study",
        help="bind a strict corpus and preregistered publication study plan",
    )
    register_study.add_argument("--corpus", type=_path, required=True)
    register_study.add_argument("--plan", type=_path, required=True)
    register_study.add_argument("--output", type=_path, required=True)

    seal_study = commands.add_parser(
        "seal-study",
        help="bind completed planned runs to sealed-label custody evidence",
    )
    seal_study.add_argument("--corpus", type=_path, required=True)
    seal_study.add_argument("--registration", type=_path, required=True)
    seal_study.add_argument(
        "--analysis-run",
        dest="analysis_runs",
        type=_path,
        action="append",
        required=True,
    )
    seal_study.add_argument(
        "--post-fix-analysis-run",
        dest="post_fix_analysis_runs",
        type=_path,
        action="append",
        required=True,
    )
    seal_study.add_argument("--plan", type=_path, required=True)
    seal_study.add_argument("--output", type=_path, required=True)

    evaluation = commands.add_parser(
        "judge-evaluation",
        help=(
            "build a blinded human audit bound to every preregistered judge "
            "decision"
        ),
    )
    evaluation.add_argument("--corpus", type=_path, required=True)
    evaluation.add_argument("--registration", type=_path, required=True)
    evaluation.add_argument("--seal-ledger", type=_path, required=True)
    evaluation.add_argument(
        "--analysis-run",
        dest="analysis_runs",
        type=_path,
        action="append",
        required=True,
    )
    evaluation.add_argument(
        "--post-fix-analysis-run",
        dest="post_fix_analysis_runs",
        type=_path,
        action="append",
        required=True,
    )
    evaluation.add_argument(
        "--judgment",
        dest="judgments",
        type=_path,
        action="append",
        required=True,
    )
    evaluation.add_argument("--plan", type=_path, required=True)
    evaluation.add_argument("--output", type=_path, required=True)

    evaluation_packet = commands.add_parser(
        "judge-evaluation-packet",
        help=(
            "export opaque decision subjects and evidence without model or "
            "machine-verdict identity"
        ),
    )
    evaluation_packet.add_argument("--corpus", type=_path, required=True)
    evaluation_packet.add_argument(
        "--registration",
        type=_path,
        required=True,
    )
    evaluation_packet.add_argument(
        "--post-fix-analysis-run",
        dest="post_fix_analysis_runs",
        type=_path,
        action="append",
        required=True,
    )
    evaluation_packet.add_argument("--seal-ledger", type=_path, required=True)
    evaluation_packet.add_argument(
        "--analysis-run",
        dest="analysis_runs",
        type=_path,
        action="append",
        required=True,
    )
    evaluation_packet.add_argument(
        "--judgment",
        dest="judgments",
        type=_path,
        action="append",
        required=True,
    )
    evaluation_packet.add_argument("--output", type=_path, required=True)

    package = commands.add_parser(
        "reproducibility-package",
        help="hash and verify a secret-free, corpus-bound rerun package",
    )
    package.add_argument("--artifact-root", type=_path, required=True)
    package.add_argument("--corpus", type=_path, required=True)
    package.add_argument("--registration", type=_path, required=True)
    package.add_argument("--seal-ledger", type=_path, required=True)
    package.add_argument("--judge-evaluation", type=_path, required=True)
    package.add_argument("--metrics", type=_path, required=True)
    package.add_argument("--dashboard", type=_path, required=True)
    package.add_argument(
        "--analysis-artifact",
        dest="analysis_artifacts",
        type=_path,
        action="append",
        required=True,
    )
    package.add_argument(
        "--judgment-artifact",
        dest="judgment_artifacts",
        type=_path,
        action="append",
        required=True,
    )
    package.add_argument(
        "--runtime-artifact",
        dest="runtime_artifacts",
        type=_path,
        action="append",
        required=True,
    )
    package.add_argument(
        "--config-artifact",
        dest="config_artifacts",
        type=_path,
        action="append",
        required=True,
    )
    for option, destination, description in (
        (
            "--source-artifact",
            "source_artifacts",
            "draft, REST source archive, and GraphQL/REST thread evidence",
        ),
        (
            "--curation-artifact",
            "curation_artifacts",
            "curation packet, decisions, and released selection",
        ),
        (
            "--replay-artifact",
            "replay_artifacts",
            "primary replay lock and live attestation",
        ),
        (
            "--current-comment-artifact",
            "current_comment_artifacts",
            "live current-review-comment attestation",
        ),
        (
            "--post-fix-artifact",
            "post_fix_artifacts",
            "verified-F replay, runs, judgments, controls, and control set",
        ),
        (
            "--execution-artifact",
            "execution_artifacts",
            "canonical label-free analysis execution corpus",
        ),
        (
            "--repository-artifact",
            "repository_artifacts",
            "immutable bare Git source reconstruction evidence root",
        ),
    ):
        package.add_argument(
            option,
            dest=destination,
            type=_path,
            action="append",
            required=True,
            help=f"{description} (file or directory; repeat as needed)",
        )
    package.add_argument(
        "--rerun-instruction",
        dest="rerun_instructions",
        action="append",
        required=True,
    )
    package.add_argument(
        "--limitation",
        dest="limitations",
        action="append",
        required=True,
    )
    package.add_argument("--output", type=_path, required=True)

    verify_package = commands.add_parser(
        "verify-reproducibility-package",
        help="recompute package files, digests, bindings, and secret scans",
    )
    verify_package.add_argument("--artifact-root", type=_path, required=True)
    verify_package.add_argument("--manifest", type=_path, required=True)
    return parser


def _github_client(
    config: dict[str, Any],
    *,
    config_path: Path | None,
    offline: bool,
) -> GitHubClient:
    root = config_path.parent if config_path is not None else Path.cwd()
    return GitHubClient.from_config(
        config["github"],
        root=root,
        offline=offline,
    )


def _dispatch(args: argparse.Namespace, config: dict[str, Any]) -> Any:
    github = config["github"]
    if args.command == "validate":
        return validate_corpus(
            read_json(args.corpus),
            paper_ready=args.paper_ready,
            required_cases=args.required_cases,
        )
    if args.command == "validate-draft":
        return validate_draft_file(args.draft)
    if args.command == "verify-current-comments":
        client = _github_client(
            config,
            config_path=args.config,
            offline=args.offline,
        )
        attestation = attest_current_comments(
            client,
            draft_path=args.draft,
            output=args.output,
            repository=str(github["repository"]),
        )
        return {
            "kind": attestation["kind"],
            "sourceMode": attestation["sourceMode"],
            "currentSelectedCommentsVerified": attestation[
                "currentSelectedCommentsVerified"
            ],
            "caseCount": attestation["caseCount"],
            "selectedRootCount": attestation["selectedRootCount"],
            "completeRestReplyCount": attestation[
                "completeRestReplyCount"
            ],
            "paperReady": attestation["paperReady"],
            "scoringEnabled": attestation["scoringEnabled"],
            "attestationDigest": attestation["attestationDigest"],
            "output": str(args.output),
        }
    if args.command == "discover":
        if args.pages < 1:
            raise ValueError("--pages must be >= 1")
        client = _github_client(
            config,
            config_path=args.config,
            offline=args.offline,
        )
        return discover(
            client,
            repository=str(github["repository"]),
            pages=args.pages,
            output=args.output,
        )
    if args.command == "link-discovery-selection":
        return link_discovery_selection(
            discovery_path=args.discovery,
            selection_path=args.selection,
            output=args.output,
        )
    if args.command == "materialize":
        client = _github_client(
            config,
            config_path=args.config,
            offline=args.offline,
        )
        required = (
            args.required_cases
            if args.required_cases is not None
            else int(config["corpus"]["required_cases"])
        )
        return materialize(
            client,
            selection_path=args.selection,
            repository_path=args.repository_path,
            output=args.output,
            repository=str(github["repository"]),
            default_branch=str(github["default_branch"]),
            required_cases=required,
        )
    if args.command == "curation-packet":
        return export_curation_packet(
            draft_path=args.draft,
            repository=args.repository_path,
            output=args.output,
            thread_evidence_path=args.thread_evidence,
        )
    if args.command == "collect-threads":
        client = _github_client(
            config,
            config_path=args.config,
            offline=args.offline,
        )
        return hydrate_review_threads(
            client,
            draft_path=args.draft,
            output=args.output,
        )
    if args.command == "archive-draft-sources":
        client = _github_client(
            config,
            config_path=args.config,
            offline=args.offline,
        )
        return archive_draft_sources(
            client,
            draft_path=args.draft,
            output=args.output,
        )
    if args.command == "release-selection":
        return release_selection(
            draft_path=args.draft,
            decisions_path=args.decisions,
            output=args.output,
            paper_ready=args.paper_ready,
            source_archive_path=args.source_archive,
            thread_evidence_path=args.thread_evidence,
            curation_packet_path=args.curation_packet,
        )
    if args.command == "execution-corpus":
        return create_execution_corpus(
            corpus_path=args.corpus,
            output=args.output,
        )
    if args.command == "repository-evidence":
        return create_repository_evidence(
            corpus_path=args.corpus,
            source_repository=args.source_repository,
            output_root=args.output_root,
        )
    if args.command == "replay-plan":
        return create_plan(
            execution_corpus_path=args.execution_corpus,
            fork_repository=args.fork,
            output=args.output,
        )
    if args.command == "replay-apply":
        client = _github_client(
            config,
            config_path=args.config,
            offline=False,
        )
        return apply_plan(
            client,
            plan_path=args.plan,
            output=args.output,
            confirm_fork=args.confirm_fork,
            source_repository=args.source_repository,
            git_remote=args.git_remote,
        )
    if args.command == "verify-replay":
        client = _github_client(
            config,
            config_path=args.config,
            offline=False,
        )
        return verify_replay(
            client,
            execution_corpus_path=args.execution_corpus,
            replay_lock_path=args.replay_lock,
            output=args.output,
        )
    if args.command == "operator-preflight":
        return operator_preflight(
            config_path=args.config,
            execution_corpus_path=args.execution_corpus,
            replay_lock_path=args.replay_lock,
            replay_attestation_path=args.replay_attestation,
            repository=args.repository_path,
            output=args.output,
        )
    if args.command == "post-fix-replay-plan":
        return create_post_fix_plan(
            corpus_path=args.corpus,
            primary_replay_lock_path=args.primary_replay_lock,
            repository=args.repository_path,
            output=args.output,
        )
    if args.command == "post-fix-replay-apply":
        client = _github_client(
            config,
            config_path=args.config,
            offline=False,
        )
        return apply_post_fix_plan(
            client,
            execution_corpus_path=args.execution_corpus,
            primary_replay_lock_path=args.primary_replay_lock,
            plan_path=args.plan,
            output=args.output,
            confirm_fork=args.confirm_fork,
            source_repository=args.source_repository,
            git_remote=args.git_remote,
        )
    if args.command == "verify-post-fix-replay":
        client = _github_client(
            config,
            config_path=args.config,
            offline=False,
        )
        return verify_post_fix_replay(
            client,
            execution_corpus_path=args.execution_corpus,
            primary_replay_lock_path=args.primary_replay_lock,
            post_fix_replay_lock_path=args.post_fix_replay_lock,
            output=args.output,
        )
    if args.command == "run":
        if args.limit is not None and args.limit < 1:
            raise ValueError("--limit must be >= 1")
        return run_analysis(
            execution_corpus_path=args.execution_corpus,
            replay_lock_path=args.replay_lock,
            replay_attestation_path=args.replay_attestation,
            repository=args.repository_path,
            output_dir=args.output_dir,
            config=config,
            run_id=args.run_id,
            model=args.analysis_model,
            expected_response_model=args.expected_analysis_response_model,
            selected_case_ids=(
                set(args.case_ids) if args.case_ids is not None else None
            ),
            limit=args.limit,
            resume=args.resume,
        )
    if args.command == "judge":
        return judge_run(
            corpus_path=args.corpus,
            run_path=args.run_path,
            repository=args.repository_path,
            output_dir=args.output_dir,
            config=config,
            judgment_id=args.judgment_id,
            model=args.judge_model,
            expected_response_model=args.expected_response_model,
        )
    if args.command == "post-fix-run":
        return run_post_fix_analysis(
            execution_corpus_path=args.execution_corpus,
            registration_path=args.registration,
            primary_replay_lock_path=args.primary_replay_lock,
            post_fix_replay_lock_path=args.post_fix_replay_lock,
            post_fix_replay_attestation_path=(
                args.post_fix_replay_attestation
            ),
            primary_run_path=args.primary_run,
            repository=args.repository_path,
            output_dir=args.output_dir,
            config=config,
            resume=args.resume,
        )
    if args.command == "post-fix-judge":
        return judge_post_fix_run(
            corpus_path=args.corpus,
            registration_path=args.registration,
            seal_ledger_path=args.seal_ledger,
            primary_run_path=args.primary_run,
            primary_judgment_path=args.primary_judgment,
            post_fix_run_path=args.post_fix_run,
            primary_replay_lock_path=args.primary_replay_lock,
            post_fix_replay_lock_path=args.post_fix_replay_lock,
            post_fix_replay_attestation_path=(
                args.post_fix_replay_attestation
            ),
            registered_primary_run_paths=args.registered_analysis_runs,
            registered_post_fix_run_paths=(
                args.registered_post_fix_analysis_runs
            ),
            repository=args.repository_path,
            output_dir=args.output_dir,
            config=config,
        )
    if args.command == "post-fix-control":
        return build_post_fix_control(
            corpus_path=args.corpus,
            registration_path=args.registration,
            seal_ledger_path=args.seal_ledger,
            primary_replay_lock_path=args.primary_replay_lock,
            primary_run_path=args.primary_run,
            primary_judgment_path=args.primary_judgment,
            post_fix_run_path=args.post_fix_run,
            post_fix_replay_lock_path=args.post_fix_replay_lock,
            post_fix_replay_attestation_path=(
                args.post_fix_replay_attestation
            ),
            post_fix_judgment_path=args.post_fix_judgment,
            repository=args.repository_path,
            output_path=args.output,
        )
    if args.command == "post-fix-control-set":
        return create_post_fix_control_set(
            manifest_path=args.manifest,
            output_path=args.output,
        )
    if args.command == "metrics":
        return build_metrics(
            corpus_path=args.corpus,
            judgment_paths=args.judgments,
            repository_path=args.repository_path,
            repository_evidence_path=args.repository_evidence,
            analysis_run_paths=args.analysis_runs,
            post_fix_analysis_run_paths=args.post_fix_analysis_runs,
            post_fix_control_set_path=args.post_fix_control_set,
            post_fix_artifact_paths=args.post_fix_artifacts,
            replay_lock_paths=args.replay_locks,
            replay_attestation_paths=args.replay_attestations,
            study_registration_path=args.study_registration,
            seal_ledger_path=args.seal_ledger,
            judge_evaluation_path=args.judge_evaluation,
            output_path=args.output,
            bootstrap_iterations=args.bootstrap_iterations,
            seed=args.seed,
        )
    if args.command == "dashboard":
        from .dashboard import build_dashboard

        return build_dashboard(
            metrics_path=args.metrics,
            output_dir=args.output_dir,
        )
    if args.command == "register-study":
        return create_study_registration(
            corpus_path=args.corpus,
            plan_path=args.plan,
            output_path=args.output,
        )
    if args.command == "seal-study":
        return create_seal_ledger(
            corpus_path=args.corpus,
            registration_path=args.registration,
            analysis_run_paths=args.analysis_runs,
            post_fix_analysis_run_paths=args.post_fix_analysis_runs,
            ledger_plan_path=args.plan,
            output_path=args.output,
        )
    if args.command == "judge-evaluation":
        return create_judge_evaluation(
            corpus_path=args.corpus,
            registration_path=args.registration,
            seal_ledger_path=args.seal_ledger,
            analysis_run_paths=args.analysis_runs,
            post_fix_analysis_run_paths=args.post_fix_analysis_runs,
            judgment_paths=args.judgments,
            evaluation_plan_path=args.plan,
            output_path=args.output,
        )
    if args.command == "judge-evaluation-packet":
        return export_judge_evaluation_packet(
            corpus_path=args.corpus,
            registration_path=args.registration,
            seal_ledger_path=args.seal_ledger,
            analysis_run_paths=args.analysis_runs,
            post_fix_analysis_run_paths=args.post_fix_analysis_runs,
            judgment_paths=args.judgments,
            output_path=args.output,
        )
    if args.command == "reproducibility-package":
        return build_reproducibility_package(
            artifact_root=args.artifact_root,
            corpus_path=args.corpus,
            registration_path=args.registration,
            seal_ledger_path=args.seal_ledger,
            judge_evaluation_path=args.judge_evaluation,
            metrics_path=args.metrics,
            dashboard_path=args.dashboard,
            analysis_artifacts=args.analysis_artifacts,
            judgment_artifacts=args.judgment_artifacts,
            runtime_artifacts=args.runtime_artifacts,
            config_artifacts=args.config_artifacts,
            source_artifacts=args.source_artifacts,
            curation_artifacts=args.curation_artifacts,
            replay_artifacts=args.replay_artifacts,
            current_comment_artifacts=args.current_comment_artifacts,
            post_fix_artifacts=args.post_fix_artifacts,
            execution_artifacts=args.execution_artifacts,
            repository_artifacts=args.repository_artifacts,
            rerun_instructions=args.rerun_instructions,
            limitations=args.limitations,
            output_path=args.output,
        )
    if args.command == "verify-reproducibility-package":
        return verify_reproducibility_package(
            artifact_root=args.artifact_root,
            manifest_path=args.manifest,
        )
    raise AssertionError(f"unhandled command: {args.command}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        result = _dispatch(args, config)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    summary = (
        result
        if isinstance(result, Mapping)
        else {"status": "completed", "result": result}
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    if args.command == "operator-preflight" and summary.get("runReady") is not True:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
