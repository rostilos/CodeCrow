from __future__ import annotations

import os
import sys
from collections.abc import Iterator, MutableMapping
from types import TracebackType
from typing import ClassVar


SENSITIVE_ENVIRONMENT_KEYS = frozenset(
    {
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "OPENROUTER_API_KEY",
        "AI_API_KEY",
        "QA_DOC_AI_API_KEY",
        "QDRANT_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
        "GITHUB_TOKEN",
        "GITHUB_APP_PRIVATE_KEY",
        "GITLAB_TOKEN",
        "BITBUCKET_TOKEN",
        "BITBUCKET_CLIENT_SECRET",
        "JIRA_TOKEN",
        "JIRA_API_TOKEN",
        "SMTP_PASSWORD",
        "SENDGRID_API_KEY",
        "NEW_RELIC_LICENSE_KEY",
        "OTEL_EXPORTER_OTLP_HEADERS",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_SECURITY_TOKEN",
        "AWS_PROFILE",
        "AWS_SHARED_CREDENTIALS_FILE",
        "AWS_CONFIG_FILE",
        "LANGSMITH_API_KEY",
        "LANGCHAIN_API_KEY",
        "HUGGINGFACEHUB_API_TOKEN",
        "HF_TOKEN",
        "COHERE_API_KEY",
        "MISTRAL_API_KEY",
        "GROQ_API_KEY",
        "TOGETHER_API_KEY",
        "DEEPSEEK_API_KEY",
        "ENV_INFERENCE_ORCHESTRATOR",
        "ENV_RAG_PIPELINE",
        "ENV_WEB_FRONTEND",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    }
)

SERVICE_SECRET_KEYS = frozenset(
    {
        "SERVICE_SECRET",
        "INTERNAL_API_SECRET",
        "CODECROW_INTERNAL_API_SECRET",
        "CODECROW_RAG_API_SECRET",
        "CODECROW_INTERNAL_SECRET",
    }
)
TEST_SERVICE_SECRET = "test-secret-token"
_APPROVED_TEST_SERVICE_SECRETS = frozenset(
    {
        TEST_SERVICE_SECRET,
        # Existing component contracts use these explicit, non-production literals.
        "test-secret",
        "my-secret",
    }
)
_APPROVED_EPHEMERAL_CREDENTIALS = frozenset({"key", "test", "test-key"})


class CredentialReintroductionError(RuntimeError):
    """A test attempted to load a real credential after sanitization."""


class _GuardedEnvironment(MutableMapping[str, str]):
    def __init__(self, delegate: MutableMapping[str, str]) -> None:
        self._delegate = delegate

    def __getitem__(self, key: str) -> str:
        return self._delegate[key]

    def __setitem__(self, key: str, value: str) -> None:
        _validate_assignment(key, value)
        self._delegate[key] = value

    def __delitem__(self, key: str) -> None:
        del self._delegate[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._delegate)

    def __len__(self) -> int:
        return len(self._delegate)

    def copy(self) -> dict[str, str]:
        return dict(self._delegate)


class CredentialScrubber:
    _active: ClassVar[bool] = False
    _active_scrubber: ClassVar[CredentialScrubber | None] = None

    def __init__(
        self,
        environ: MutableMapping[str, str] | None = None,
        *,
        populate_service_secrets: bool = True,
    ) -> None:
        self._environment = environ if environ is not None else os.environ
        self._populate_service_secrets = populate_service_secrets
        self._snapshot: dict[str, tuple[bool, str]] = {}
        self._original_os_environ: MutableMapping[str, str] | None = None
        self._entered = False

    def __enter__(self) -> CredentialScrubber:
        if self._entered or CredentialScrubber._active:
            raise RuntimeError("another credential scrubber is already active")
        CredentialScrubber._active = True
        CredentialScrubber._active_scrubber = self
        self._entered = True
        managed = SENSITIVE_ENVIRONMENT_KEYS | SERVICE_SECRET_KEYS
        self._snapshot = {
            key: (key in self._environment, self._environment.get(key, "")) for key in managed
        }
        for key in SENSITIVE_ENVIRONMENT_KEYS:
            self._environment[key] = ""
        for key in SERVICE_SECRET_KEYS:
            current = self._environment.get(key)
            if self._populate_service_secrets:
                self._environment[key] = TEST_SERVICE_SECRET
            elif current and current not in _APPROVED_TEST_SERVICE_SECRETS:
                self._environment[key] = TEST_SERVICE_SECRET
        if self._environment is os.environ:
            self._original_os_environ = os.environ
            os.environ = _GuardedEnvironment(self._environment)  # type: ignore[assignment]
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if not self._entered:
            return
        CredentialScrubber._active_scrubber = None
        if self._original_os_environ is not None:
            os.environ = self._original_os_environ  # type: ignore[assignment]
        for key, (existed, value) in self._snapshot.items():
            if existed:
                self._environment[key] = value
            else:
                self._environment.pop(key, None)
        self._entered = False
        CredentialScrubber._active = False

    def assert_sanitized(self) -> None:
        populated = [key for key in SENSITIVE_ENVIRONMENT_KEYS if self._environment.get(key)]
        if self._populate_service_secrets:
            invalid_service = [
                key
                for key in SERVICE_SECRET_KEYS
                if self._environment.get(key) != TEST_SERVICE_SECRET
            ]
        else:
            invalid_service = [
                key
                for key in SERVICE_SECRET_KEYS
                if self._environment.get(key, "")
                not in ({""} | _APPROVED_TEST_SERVICE_SECRETS)
            ]
        if populated or invalid_service:
            keys = ", ".join(sorted(populated + invalid_service))
            raise CredentialReintroductionError(
                f"offline credential policy violated by environment key(s): {keys}"
            )


def _validate_assignment(key: str, value: str) -> None:
    if (
        key in SENSITIVE_ENVIRONMENT_KEYS
        and value
        and value not in _APPROVED_EPHEMERAL_CREDENTIALS
    ):
        raise CredentialReintroductionError(
            f"credential environment key {key} cannot be populated in offline tests"
        )
    if key in SERVICE_SECRET_KEYS and value not in ({""} | _APPROVED_TEST_SERVICE_SECRETS):
        raise CredentialReintroductionError(
            f"service secret key {key} must use the deterministic test value"
        )


def _credential_audit_hook(event: str, arguments: tuple[object, ...]) -> None:
    if event != "os.putenv" or CredentialScrubber._active_scrubber is None:
        return
    _validate_assignment(os.fsdecode(arguments[0]), os.fsdecode(arguments[1]))


_credential_audit_hook.__cantrace__ = True  # type: ignore[attr-defined]
sys.addaudithook(_credential_audit_hook)
