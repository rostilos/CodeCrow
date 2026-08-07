"""Provider-independent fixtures for neutral prompt-capture replay tests."""

from __future__ import annotations

from model.dtos import ReviewRequestDto
from model.enrichment import FileContentDto, PrEnrichmentDataDto


SECRET_API_KEY = "dry-run-provider-key-must-never-be-used-or-returned"
HEAD_REVISION = "1" * 40
BASE_REVISION = "2" * 40
BASE_GENERATION_MANIFEST = "3" * 64
PR_GENERATION_FINGERPRINT = "sha256:" + "4" * 64
PR_OVERLAY_GENERATION_MANIFEST = "5" * 64
BASE_PLUGIN_FINGERPRINT = "sha256:" + "6" * 64
BASE_PLUGIN_DESCRIPTOR_FINGERPRINT = "sha256:" + "7" * 64
BASE_PLUGIN_IMPLEMENTATION_FINGERPRINT = "sha256:" + "8" * 64
BASE_INDEX_REPRESENTATION_FINGERPRINT = "sha256:" + "9" * 64


class DeterministicRagSpy:
    def __init__(self):
        self.requests: list[dict] = []
        self.semantic_requests: list[dict] = []
        self.index_requests: list[dict] = []
        self.delete_requests: list[dict] = []

    async def get_deterministic_context(self, **kwargs):
        self.requests.append(kwargs)
        return {
            "context": {
                "chunks": [{
                    "path": "src/shared.py",
                    "content": "SHARED_CONTEXT_SENTINEL = True",
                    "relationship": "imports",
                }],
                "changed_files": {},
                "related_definitions": {},
                "_metadata": {"retrieval_state": "complete"},
            }
        }

    async def get_pr_context(self, **kwargs):
        self.semantic_requests.append(kwargs)
        return {"context": {"relevant_code": []}}

    async def search_for_duplicates(self, **_kwargs):
        return []

    async def index_pr_files(self, **kwargs):
        self.index_requests.append(kwargs)
        return {
            "status": "indexed",
            "chunks_indexed": 0,
            "base_generation_manifest_sha256": BASE_GENERATION_MANIFEST,
            "generation_fingerprint": PR_GENERATION_FINGERPRINT,
            "overlay_generation_manifest_sha256": (
                PR_OVERLAY_GENERATION_MANIFEST
            ),
            "plugin_fingerprint": BASE_PLUGIN_FINGERPRINT,
            "plugin_descriptor_fingerprint": (
                BASE_PLUGIN_DESCRIPTOR_FINGERPRINT
            ),
            "plugin_implementation_fingerprint": (
                BASE_PLUGIN_IMPLEMENTATION_FINGERPRINT
            ),
            "index_representation_fingerprint": (
                BASE_INDEX_REPRESENTATION_FINGERPRINT
            ),
        }

    async def delete_pr_files(self, **kwargs):
        self.delete_requests.append(kwargs)
        return {"status": "deleted"}


def neutral_request(
    file_count: int = 1,
    *,
    use_mcp_tools: bool = False,
) -> ReviewRequestDto:
    paths = [f"src/file_{index}.py" for index in range(file_count)]
    contents = [
        FileContentDto(
            path=path,
            content=f"value_{index} = {index + 1}\n",
            sizeBytes=len(f"value_{index} = {index + 1}\n"),
        )
        for index, path in enumerate(paths)
    ]
    diffs = []
    for index, path in enumerate(paths):
        diffs.append(
            "\n".join([
                f"diff --git a/{path} b/{path}",
                "index 1111111..2222222 100644",
                f"--- a/{path}",
                f"+++ b/{path}",
                "@@ -1 +1 @@",
                f"-value_{index} = 0",
                f"+value_{index} = {index + 1}",
            ])
        )
    return ReviewRequestDto(
        projectId=1,
        projectVcsWorkspace="workspace",
        projectVcsRepoSlug="repository",
        projectWorkspace="workspace",
        projectNamespace="project",
        aiProvider="OPENAI",
        aiModel="provider-model",
        aiApiKey=SECRET_API_KEY,
        targetBranchName="main",
        sourceBranchName="feature/dry-run",
        pullRequestId=42,
        currentCommitHash=HEAD_REVISION,
        baseCommitHash=BASE_REVISION,
        changedFiles=paths,
        rawDiff="\n".join(diffs) + "\n",
        enrichmentData=PrEnrichmentDataDto(fileContents=contents),
        useMcpTools=use_mcp_tools,
    )


def mixed_language_request() -> ReviewRequestDto:
    sources = {
        "service/account.py": "def enabled(account):\n    return account.active\n",
        "backend/src/main/java/example/Account.java": (
            "package example;\n"
            "public record Account(boolean active) {}\n"
        ),
        "web/src/account.ts": (
            "export const enabled = (account: Account) => account.active;\n"
        ),
    }
    old_lines = {
        "service/account.py": "    return False",
        "backend/src/main/java/example/Account.java": (
            "public record Account(boolean active, boolean legacy) {}"
        ),
        "web/src/account.ts": (
            "export const enabled = (_account: Account) => false;"
        ),
    }
    new_lines = {
        "service/account.py": "    return account.active",
        "backend/src/main/java/example/Account.java": (
            "public record Account(boolean active) {}"
        ),
        "web/src/account.ts": (
            "export const enabled = (account: Account) => account.active;"
        ),
    }
    diffs = []
    for path in sources:
        diffs.append("\n".join([
            f"diff --git a/{path} b/{path}",
            "index 1111111..2222222 100644",
            f"--- a/{path}",
            f"+++ b/{path}",
            "@@ -1 +1 @@",
            f"-{old_lines[path]}",
            f"+{new_lines[path]}",
        ]))

    return neutral_request().model_copy(update={
        "changedFiles": list(sources),
        "rawDiff": "\n".join(diffs) + "\n",
        "enrichmentData": PrEnrichmentDataDto(fileContents=[
            FileContentDto(
                path=path,
                content=content,
                sizeBytes=len(content.encode("utf-8")),
            )
            for path, content in sources.items()
        ]),
        "prTitle": "Neutral mixed-language pipeline gate",
    })
