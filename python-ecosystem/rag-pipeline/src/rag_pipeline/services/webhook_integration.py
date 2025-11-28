"""
Integration service to connect RAG pipeline with CodeCrow pipeline agent
"""

import logging
from typing import List, Dict, Optional
from pathlib import Path
import tempfile
import shutil

from ..models.config import RAGConfig
from ..core.index_manager import RAGIndexManager
from .query_service import RAGQueryService

logger = logging.getLogger(__name__)


class WebhookIntegration:
    """Handles webhook events and manages RAG indexing"""

    def __init__(self, config: RAGConfig):
        self.config = config
        self.index_manager = RAGIndexManager(config)
        self.query_service = RAGQueryService(config)

    def handle_pr_created(
        self,
        workspace: str,
        project: str,
        branch: str,
        commit: str,
        repo_clone_path: str,
        pr_description: Optional[str] = None
    ) -> Dict:
        """
        Handle PR created event - index entire repository

        Args:
            workspace: Workspace slug
            project: Project key
            branch: Branch name
            commit: Commit SHA
            repo_clone_path: Path to cloned repository
            pr_description: PR description (optional)

        Returns:
            Dict with indexing stats and initial context
        """
        logger.info(f"Handling PR created for {workspace}/{project}/{branch}")

        try:
            stats = self.index_manager.index_repository(
                repo_path=repo_clone_path,
                workspace=workspace,
                project=project,
                branch=branch,
                commit=commit
            )

            logger.info(f"Indexed {stats.document_count} documents, {stats.chunk_count} chunks")

            return {
                "success": True,
                "stats": stats.dict(),
                "message": f"Indexed {stats.document_count} documents"
            }

        except Exception as e:
            logger.error(f"Error handling PR created: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    def handle_pr_updated(
        self,
        workspace: str,
        project: str,
        branch: str,
        commit: str,
        changed_files: List[str],
        repo_clone_path: str
    ) -> Dict:
        """
        Handle PR updated event - incrementally update changed files

        Args:
            workspace: Workspace slug
            project: Project key
            branch: Branch name
            commit: Commit SHA
            changed_files: List of changed file paths
            repo_clone_path: Path to cloned repository

        Returns:
            Dict with update stats
        """
        logger.info(f"Handling PR updated for {workspace}/{project}/{branch}")
        logger.info(f"Changed files: {len(changed_files)}")

        try:
            added_modified = []
            deleted = []

            for file_path in changed_files:
                full_path = Path(repo_clone_path) / file_path
                if full_path.exists():
                    added_modified.append(file_path)
                else:
                    deleted.append(file_path)

            if deleted:
                self.index_manager.delete_files(
                    file_paths=deleted,
                    workspace=workspace,
                    project=project,
                    branch=branch
                )
                logger.info(f"Deleted {len(deleted)} files from index")

            if added_modified:
                stats = self.index_manager.update_files(
                    file_paths=added_modified,
                    repo_base=repo_clone_path,
                    workspace=workspace,
                    project=project,
                    branch=branch,
                    commit=commit
                )
                logger.info(f"Updated {len(added_modified)} files")
            else:
                stats = self.index_manager._get_index_stats(workspace, project, branch)

            return {
                "success": True,
                "stats": stats.dict(),
                "files_updated": len(added_modified),
                "files_deleted": len(deleted)
            }

        except Exception as e:
            logger.error(f"Error handling PR updated: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    def get_pr_analysis_context(
        self,
        workspace: str,
        project: str,
        branch: str,
        changed_files: List[str],
        pr_description: Optional[str] = None,
        top_k: int = 10
    ) -> str:
        """
        Get relevant context for PR analysis

        Args:
            workspace: Workspace slug
            project: Project key
            branch: Branch name
            changed_files: List of changed file paths
            pr_description: PR description (optional)
            top_k: Number of top results to return

        Returns:
            Formatted context string for LLM
        """
        logger.info(f"Getting PR analysis context for {workspace}/{project}/{branch}")

        try:
            context = self.query_service.get_context_for_pr(
                workspace=workspace,
                project=project,
                branch=branch,
                changed_files=changed_files,
                pr_description=pr_description,
                top_k=top_k
            )

            return context

        except Exception as e:
            logger.error(f"Error getting PR context: {e}")
            return f"Error retrieving context: {str(e)}"

    def handle_branch_deleted(
        self,
        workspace: str,
        project: str,
        branch: str
    ) -> Dict:
        """
        Handle branch deleted event - clean up index

        Args:
            workspace: Workspace slug
            project: Project key
            branch: Branch name

        Returns:
            Dict with deletion result
        """
        logger.info(f"Handling branch deleted for {workspace}/{project}/{branch}")

        try:
            self.index_manager.delete_index(workspace, project, branch)

            return {
                "success": True,
                "message": f"Deleted index for {workspace}/{project}/{branch}"
            }

        except Exception as e:
            logger.error(f"Error handling branch deleted: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    def get_file_context(
        self,
        workspace: str,
        project: str,
        branch: str,
        file_path: str,
        top_k: int = 5
    ) -> str:
        """
        Get context related to a specific file

        Args:
            workspace: Workspace slug
            project: Project key
            branch: Branch name
            file_path: File path
            top_k: Number of results

        Returns:
            Formatted context string
        """
        context = self.query_service.get_context_for_files(
            workspace=workspace,
            project=project,
            branch=branch,
            file_paths=[file_path],
            top_k=top_k
        )

        return context

