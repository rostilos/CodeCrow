"""
Prompt logging utility for debugging code review prompts.
Logs full prompts including RAG context to console and/or file.
"""
import os
import logging
from datetime import datetime
from typing import Optional, Dict, Any
from pathlib import Path

logger = logging.getLogger(__name__)

# Configuration from environment
PROMPT_LOG_ENABLED = os.environ.get("PROMPT_LOG_ENABLED", "true").lower() == "true"
PROMPT_LOG_TO_FILE = os.environ.get("PROMPT_LOG_TO_FILE", "true").lower() == "true"
PROMPT_LOG_TO_CONSOLE = os.environ.get("PROMPT_LOG_TO_CONSOLE", "false").lower() == "true"
PROMPT_LOG_DIR = os.environ.get("PROMPT_LOG_DIR", "/tmp/codecrow_prompts")
PROMPT_LOG_MAX_FILES = int(os.environ.get("PROMPT_LOG_MAX_FILES", "50"))


class PromptLogger:
    """
    Logger for debugging full prompts sent to LLM.
    Useful for debugging RAG context, reranking, and Lost-in-Middle protection.
    """
    
    @classmethod
    def log_prompt(
        cls,
        prompt: str,
        metadata: Optional[Dict[str, Any]] = None,
        stage: str = "full_prompt"
    ) -> Optional[str]:
        """
        Log a prompt for debugging.
        
        Args:
            prompt: The full prompt text
            metadata: Optional metadata (workspace, repo, PR, model, etc.)
            stage: Stage identifier (e.g., "full_prompt", "rag_context", "reranked")
            
        Returns:
            Path to log file if written, None otherwise
        """
        if not PROMPT_LOG_ENABLED:
            return None
        
        metadata = metadata or {}
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        
        # Build log header
        header_lines = [
            "=" * 80,
            f"PROMPT LOG - {stage.upper()}",
            f"Timestamp: {datetime.now().isoformat()}",
            "=" * 80,
        ]
        
        # Add metadata
        if metadata:
            header_lines.append("METADATA:")
            for key, value in metadata.items():
                header_lines.append(f"  {key}: {value}")
            header_lines.append("-" * 80)
        
        # Add stats
        header_lines.extend([
            "STATISTICS:",
            f"  Prompt length: {len(prompt)} chars",
            f"  Estimated tokens: ~{int(len(prompt) * 0.25)}",
            f"  Line count: {prompt.count(chr(10)) + 1}",
            "-" * 80,
            "FULL PROMPT:",
            "-" * 80,
        ])
        
        header = "\n".join(header_lines)
        footer = "\n" + "=" * 80 + "\nEND PROMPT LOG\n" + "=" * 80
        
        full_log = f"{header}\n{prompt}{footer}"
        
        log_file_path = None
        
        # Log to console
        if PROMPT_LOG_TO_CONSOLE:
            print(full_log)
        
        # Log to file
        if PROMPT_LOG_TO_FILE:
            log_file_path = cls._write_to_file(full_log, metadata, timestamp, stage)
        
        # Also log summary to standard logger
        workspace = metadata.get("workspace", "unknown")
        repo = metadata.get("repo", "unknown")
        pr_id = metadata.get("pr_id", "unknown")
        model = metadata.get("model", "unknown")
        
        logger.info(
            f"[PROMPT_LOG] {stage} | {workspace}/{repo}/PR#{pr_id} | "
            f"model={model} | chars={len(prompt)} | est_tokens=~{int(len(prompt) * 0.25)}"
        )
        
        return log_file_path
    
    @classmethod
    def log_rag_context(
        cls,
        rag_context: Optional[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]] = None,
        stage: str = "rag_context"
    ) -> Optional[str]:
        """
        Log RAG context separately for debugging.
        
        Args:
            rag_context: RAG context dictionary
            metadata: Optional metadata
            stage: Stage identifier
            
        Returns:
            Path to log file if written, None otherwise
        """
        if not PROMPT_LOG_ENABLED or not rag_context:
            return None
        
        import json
        
        # Build RAG summary
        relevant_code = rag_context.get("relevant_code", [])
        related_files = rag_context.get("related_files", [])
        
        lines = [
            "RAG CONTEXT SUMMARY:",
            f"  Total chunks: {len(relevant_code)}",
            f"  Related files: {len(related_files)}",
            "",
            "CHUNKS DETAIL:",
        ]
        
        for i, chunk in enumerate(relevant_code):
            path = chunk.get("metadata", {}).get("path", "unknown")
            score = chunk.get("score", 0)
            priority = chunk.get("_priority", "MEDIUM")
            boost_reason = chunk.get("_boost_reason", "none")
            text_preview = chunk.get("text", "")[:200].replace("\n", "\\n")
            
            lines.extend([
                f"\n--- Chunk {i+1} ---",
                f"  Path: {path}",
                f"  Score: {score:.4f}",
                f"  Priority: {priority}",
                f"  Boost reason: {boost_reason}",
                f"  Preview: {text_preview}...",
            ])
        
        lines.extend([
            "",
            "-" * 40,
            "FULL RAG JSON:",
            "-" * 40,
            json.dumps(rag_context, indent=2, default=str)
        ])
        
        rag_log = "\n".join(lines)
        
        return cls.log_prompt(rag_log, metadata, stage)
    
    @classmethod
    def log_structured_context(
        cls,
        structured_context: Optional[str],
        metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """
        Log structured context (Lost-in-Middle protected format).
        
        Args:
            structured_context: The structured context string
            metadata: Optional metadata
            
        Returns:
            Path to log file if written, None otherwise
        """
        if not PROMPT_LOG_ENABLED or not structured_context:
            return None
        
        return cls.log_prompt(
            structured_context, 
            metadata, 
            stage="structured_context"
        )
    
    @classmethod
    def log_llm_response(
        cls,
        response: str,
        metadata: Optional[Dict[str, Any]] = None,
        is_raw: bool = True
    ) -> Optional[str]:
        """
        Log LLM response for debugging.
        
        Args:
            response: The LLM response text
            metadata: Optional metadata
            is_raw: Whether this is raw response or parsed
            
        Returns:
            Path to log file if written, None otherwise
        """
        if not PROMPT_LOG_ENABLED:
            return None
        
        stage = "llm_response_raw" if is_raw else "llm_response_parsed"
        
        lines = [
            f"LLM RESPONSE ({stage.upper()}):",
            f"Response length: {len(response)} chars",
            "-" * 40,
            response
        ]
        
        return cls.log_prompt("\n".join(lines), metadata, stage)
    
    @classmethod
    def log_mcp_interaction(
        cls,
        tool_name: str,
        tool_input: Any,
        tool_output: Any,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """
        Log MCP tool interaction for debugging.
        
        Args:
            tool_name: Name of the MCP tool called
            tool_input: Input parameters to the tool
            tool_output: Output from the tool
            metadata: Optional metadata
            
        Returns:
            Path to log file if written, None otherwise
        """
        if not PROMPT_LOG_ENABLED:
            return None
        
        import json
        
        output_str = str(tool_output)
        output_preview = output_str[:5000] if len(output_str) > 5000 else output_str
        
        lines = [
            f"MCP TOOL INTERACTION:",
            f"Tool: {tool_name}",
            "-" * 40,
            "INPUT:",
            json.dumps(tool_input, indent=2, default=str) if tool_input else "None",
            "-" * 40,
            f"OUTPUT (length: {len(output_str)} chars):",
            output_preview,
            "..." if len(output_str) > 5000 else "",
        ]
        
        return cls.log_prompt("\n".join(lines), metadata, stage=f"mcp_{tool_name}")
    
    @classmethod
    def _write_to_file(
        cls,
        content: str,
        metadata: Optional[Dict[str, Any]],
        timestamp: str,
        stage: str
    ) -> Optional[str]:
        """Write log content to file."""
        try:
            # Ensure log directory exists
            log_dir = Path(PROMPT_LOG_DIR)
            log_dir.mkdir(parents=True, exist_ok=True)
            
            # Build filename
            workspace = (metadata or {}).get("workspace", "unknown")
            repo = (metadata or {}).get("repo", "unknown")
            pr_id = (metadata or {}).get("pr_id", "unknown")
            
            filename = f"{timestamp}_{workspace}_{repo}_PR{pr_id}_{stage}.log"
            filepath = log_dir / filename
            
            # Write file
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            
            # Cleanup old files if needed
            cls._cleanup_old_files(log_dir)
            
            logger.debug(f"Prompt logged to: {filepath}")
            return str(filepath)
            
        except Exception as e:
            logger.warning(f"Failed to write prompt log: {e}")
            return None
    
    @classmethod
    def _cleanup_old_files(cls, log_dir: Path) -> None:
        """Remove oldest log files if exceeding max count."""
        try:
            log_files = sorted(log_dir.glob("*.log"), key=lambda f: f.stat().st_mtime)
            
            if len(log_files) > PROMPT_LOG_MAX_FILES:
                files_to_remove = log_files[:-PROMPT_LOG_MAX_FILES]
                for f in files_to_remove:
                    f.unlink()
                logger.debug(f"Cleaned up {len(files_to_remove)} old prompt log files")
                
        except Exception as e:
            logger.warning(f"Failed to cleanup old prompt logs: {e}")
