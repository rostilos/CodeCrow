"""
Example usage of the RAG pipeline
"""

from pathlib import Path
from rag_pipeline.models.config import RAGConfig
from rag_pipeline.core.index_manager import RAGIndexManager
from rag_pipeline.services.query_service import RAGQueryService
from rag_pipeline.services.webhook_integration import WebhookIntegration


def example_index_repository():
    """Example: Index a repository"""
    print("=== Example: Index Repository ===\n")

    config = RAGConfig()
    index_manager = RAGIndexManager(config)

    stats = index_manager.index_repository(
        repo_path="/path/to/your/repo",
        workspace="codecrow",
        project="demo",
        branch="main",
        commit="abc123"
    )

    print(f"Indexed {stats.document_count} documents")
    print(f"Created {stats.chunk_count} chunks")
    print(f"Namespace: {stats.namespace}")
    print()


def example_incremental_update():
    """Example: Incrementally update files"""
    print("=== Example: Incremental Update ===\n")

    config = RAGConfig()
    index_manager = RAGIndexManager(config)

    changed_files = ["src/main.py", "src/utils.py"]

    stats = index_manager.update_files(
        file_paths=changed_files,
        repo_base="/path/to/your/repo",
        workspace="codecrow",
        project="demo",
        branch="feature/new",
        commit="def456"
    )

    print(f"Updated {len(changed_files)} files")
    print(f"Total chunks: {stats.chunk_count}")
    print()


def example_semantic_search():
    """Example: Perform semantic search"""
    print("=== Example: Semantic Search ===\n")

    config = RAGConfig()
    query_service = RAGQueryService(config)

    results = query_service.semantic_search(
        query="authentication implementation",
        workspace="codecrow",
        project="demo",
        branch="main",
        top_k=5
    )

    for i, result in enumerate(results, 1):
        print(f"Result {i} (Score: {result['score']:.3f})")
        print(f"File: {result['metadata']['path']}")
        print(f"Language: {result['metadata']['language']}")
        print(f"Preview: {result['text'][:100]}...")
        print()


def example_pr_context():
    """Example: Get context for PR review"""
    print("=== Example: PR Context ===\n")

    config = RAGConfig()
    query_service = RAGQueryService(config)

    context = query_service.get_context_for_pr(
        workspace="codecrow",
        project="demo",
        branch="main",
        changed_files=["src/auth.py", "src/user.py"],
        pr_description="Implemented new JWT authentication",
        top_k=10
    )

    print("Context for PR review:")
    print(context)
    print()


def example_webhook_integration():
    """Example: Webhook integration"""
    print("=== Example: Webhook Integration ===\n")

    config = RAGConfig()
    webhook = WebhookIntegration(config)

    # Handle PR created
    result = webhook.handle_pr_created(
        workspace="codecrow",
        project="demo",
        branch="feature/new",
        commit="abc123",
        repo_clone_path="/tmp/repo_clone",
        pr_description="New feature implementation"
    )

    print(f"PR Created: {result['success']}")
    if result['success']:
        print(f"Message: {result['message']}")
        print(f"Stats: {result['stats']}")

    # Handle PR updated
    result = webhook.handle_pr_updated(
        workspace="codecrow",
        project="demo",
        branch="feature/new",
        commit="def456",
        changed_files=["src/main.py", "src/utils.py"],
        repo_clone_path="/tmp/repo_clone"
    )

    print(f"\nPR Updated: {result['success']}")
    if result['success']:
        print(f"Files updated: {result['files_updated']}")
        print(f"Files deleted: {result['files_deleted']}")

    # Get context for analysis
    context = webhook.get_pr_analysis_context(
        workspace="codecrow",
        project="demo",
        branch="feature/new",
        changed_files=["src/main.py"],
        pr_description="Updated main logic"
    )

    print(f"\nAnalysis Context Length: {len(context)} characters")
    print()


def example_list_indices():
    """Example: List all indices"""
    print("=== Example: List Indices ===\n")

    config = RAGConfig()
    index_manager = RAGIndexManager(config)

    indices = index_manager.list_indices()

    print(f"Found {len(indices)} indices:")
    for idx in indices:
        print(f"- {idx.namespace}")
        print(f"  Workspace: {idx.workspace}, Project: {idx.project}, Branch: {idx.branch}")
        print(f"  Documents: {idx.document_count}, Chunks: {idx.chunk_count}")
        print(f"  Last Updated: {idx.last_updated}")
        print()


def example_delete_index():
    """Example: Delete an index"""
    print("=== Example: Delete Index ===\n")

    config = RAGConfig()
    index_manager = RAGIndexManager(config)

    index_manager.delete_index(
        workspace="codecrow",
        project="demo",
        branch="old-feature"
    )

    print("Index deleted successfully")
    print()


if __name__ == "__main__":
    print("CodeCrow RAG Pipeline - Examples\n")
    print("Note: Update the paths and credentials before running\n")

    # Uncomment the examples you want to run:

    # example_index_repository()
    # example_incremental_update()
    # example_semantic_search()
    # example_pr_context()
    # example_webhook_integration()
    # example_list_indices()
    # example_delete_index()

    print("Done!")

