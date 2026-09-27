"""Resolve Vertex model identifiers, location metadata, and credentials."""
import json
import os
from typing import Any, Optional
from urllib.parse import urlparse


GOOGLE_VERTEX_SCOPES = ("https://www.googleapis.com/auth/cloud-platform",)


def _strip_google_vertex_model_prefix(ai_model: str) -> str:
    """Accept common Vertex resource forms and return the bare model id."""
    model = ai_model.strip()
    prefixes = (
        "publishers/google/models/",
        "models/",
    )
    if "/publishers/google/models/" in model:
        model = model.rsplit("/publishers/google/models/", 1)[1]
    for prefix in prefixes:
        if model.startswith(prefix):
            return model[len(prefix):]
    return model


def _parse_google_vertex_config(ai_base_url: Optional[str]) -> tuple[Optional[str], str]:
    """
    Parse Vertex project/location metadata from the existing aiBaseUrl field.

    Accepted values include:
    - project-id/global
    - project-id:global
    - projects/project-id/locations/global
    - a full Vertex API URL containing /projects/{project}/locations/{location}
    - JSON with project/project_id and location/region
    """
    project = (
        os.environ.get("GOOGLE_VERTEX_PROJECT")
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GCLOUD_PROJECT")
    )
    location = (
        os.environ.get("GOOGLE_VERTEX_LOCATION")
        or os.environ.get("GOOGLE_CLOUD_LOCATION")
        or "global"
    )

    if not ai_base_url or not ai_base_url.strip():
        return project, location

    value = ai_base_url.strip()

    if value.startswith("{"):
        data = json.loads(value)
        project = data.get("project") or data.get("project_id") or project
        location = data.get("location") or data.get("region") or location
        return project, location

    parsed_url = urlparse(value)
    if parsed_url.scheme and parsed_url.netloc:
        value = parsed_url.path.strip("/")

    parts = [part for part in value.strip("/").split("/") if part]
    if "projects" in parts and "locations" in parts:
        project_index = parts.index("projects") + 1
        location_index = parts.index("locations") + 1
        if project_index < len(parts):
            project = parts[project_index]
        if location_index < len(parts):
            location = parts[location_index]
        return project, location

    if "/" in value:
        project_part, location_part = value.split("/", 1)
        return project_part.strip() or project, location_part.strip() or location

    if ":" in value:
        project_part, location_part = value.split(":", 1)
        return project_part.strip() or project, location_part.strip() or location

    return value, location


def _build_google_vertex_credentials(ai_api_key: str) -> tuple[Any, Optional[str], Optional[str]]:
    """Build Vertex auth from service-account JSON, ADC, or an express API key."""
    credential_value = (ai_api_key or "").strip()
    if credential_value.lower() in {"adc", "application_default", "application-default"}:
        return None, None, None

    if credential_value.startswith("{"):
        from google.oauth2 import service_account

        service_account_info = json.loads(credential_value)
        credentials = service_account.Credentials.from_service_account_info(
            service_account_info,
            scopes=list(GOOGLE_VERTEX_SCOPES),
        )
        return credentials, service_account_info.get("project_id"), None

    return None, None, credential_value or None
