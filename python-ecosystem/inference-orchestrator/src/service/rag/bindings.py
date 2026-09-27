"""Host-owned payload bindings shared by RAG endpoint families."""
from __future__ import annotations
import asyncio
import logging
import os
from typing import Dict, List, Optional, Any
import httpx

logger = logging.getLogger(__name__)

def structural_query_payload(
    *,
    workspace: str,
    project: str,
    branch: str,
    repository_revision: Optional[str],
    repository_generation_manifest_sha256: Optional[str],
    collection_target: Optional[str],
) -> Dict[str, Any]:
    """Build the server-owned repository-generation binding."""
    payload: Dict[str, Any] = {
        "workspace": workspace,
        "project": project,
        "branch": branch,
    }
    if repository_revision:
        payload["repository_revision"] = repository_revision
    if repository_generation_manifest_sha256:
        payload["repository_generation_manifest_sha256"] = (
            repository_generation_manifest_sha256
        )
    if collection_target:
        payload["collection_target"] = collection_target
    return payload


def review_query_payload(
    *,
    workspace: str,
    project: str,
    target_branch: str,
    base_revision: str,
    source_revision: str,
    target_repo_path: str,
    review_overlay_path: str,
    focus_paths: Optional[List[str]] = None,
    base_collection_target: Optional[str] = None,
    base_generation_manifest_sha256: Optional[str] = None,
    review_collection_target: Optional[str] = None,
    review_generation_manifest_sha256: Optional[str] = None,
    include_patterns: Optional[List[str]] = None,
    exclude_patterns: Optional[List[str]] = None,
    project_type: Optional[str] = None,
    source_root: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the host-controlled exact proposed-tree binding."""
    payload: Dict[str, Any] = {
        "workspace": workspace,
        "project": project,
        "target_branch": target_branch,
        "base_revision": base_revision,
        "source_revision": source_revision,
        "target_repo_path": target_repo_path,
        "review_overlay_path": review_overlay_path,
    }
    if focus_paths is not None:
        payload["focus_paths"] = focus_paths
    for key, value in (
        ("base_collection_target", base_collection_target),
        (
            "base_generation_manifest_sha256",
            base_generation_manifest_sha256,
        ),
        ("review_collection_target", review_collection_target),
        (
            "review_generation_manifest_sha256",
            review_generation_manifest_sha256,
        ),
        ("include_patterns", include_patterns),
        ("exclude_patterns", exclude_patterns),
        ("project_type", project_type),
        ("source_root", source_root),
    ):
        if value is not None:
            payload[key] = value
    return payload


