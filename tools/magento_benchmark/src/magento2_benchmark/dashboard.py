"""Generate the benchmark's zero-build static dashboard bundle."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .metrics import METRICS_KIND
from .util import sha256_json


ASSET_NAMES = ("index.html", "app.js", "styles.css")


class DashboardError(ValueError):
    """Raised when a dashboard bundle cannot be generated safely."""


def _asset_directory() -> Path:
    return Path(__file__).resolve().parent / "dashboard_assets"


def _load_summary(summary_path: Path) -> dict[str, Any]:
    if not summary_path.is_file():
        raise DashboardError(f"metrics summary does not exist: {summary_path}")

    try:
        document = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DashboardError(
            f"metrics summary is not valid UTF-8 JSON: {summary_path}"
        ) from exc

    if not isinstance(document, dict):
        raise DashboardError("metrics summary must be a JSON object")
    if document.get("kind") != METRICS_KIND:
        raise DashboardError(f"metrics summary kind must be {METRICS_KIND}")
    digest_payload = dict(document)
    declared_digest = digest_payload.pop("metricsDigest", None)
    if declared_digest != sha256_json(digest_payload):
        raise DashboardError("metrics summary digest is missing or invalid")
    if not isinstance(document.get("configurations"), list):
        raise DashboardError("metrics summary must contain a configurations array")
    return document


def generate_dashboard(
    summary_path: str | Path,
    output_directory: str | Path,
) -> dict[str, str]:
    """Copy a metrics summary and the static UI assets into ``output_directory``.

    The summary is validated before any output is created. Its original bytes are
    copied to ``data.json`` so the dashboard is an auditable view of the metrics
    artifact rather than a second metrics transformation.
    """

    source = Path(summary_path).expanduser().resolve()
    destination = Path(output_directory).expanduser().resolve()
    _load_summary(source)

    assets = _asset_directory()
    missing_assets = [name for name in ASSET_NAMES if not (assets / name).is_file()]
    if missing_assets:
        missing = ", ".join(missing_assets)
        raise DashboardError(f"dashboard source assets are missing: {missing}")

    destination.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination / "data.json")
    for asset_name in ASSET_NAMES:
        shutil.copyfile(assets / asset_name, destination / asset_name)

    return {
        "index": str(destination / "index.html"),
        "data": str(destination / "data.json"),
        "script": str(destination / "app.js"),
        "styles": str(destination / "styles.css"),
    }


def build_dashboard(
    *,
    metrics_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """CLI-friendly keyword-only wrapper around :func:`generate_dashboard`."""

    return generate_dashboard(metrics_path, output_dir)
