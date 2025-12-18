"""
MCP Server Process Pool Manager.

This module provides efficient process pooling for MCP servers to avoid
JVM startup overhead on each request. Critical for SaaS deployments
serving dozens of teams and hundreds of developers.

Architecture:
- Maintains a pool of pre-warmed MCP server processes
- Each process stays alive and handles multiple requests
- Requests are routed to available processes via STDIO
- Processes are recycled after N requests or on error

Performance Impact:
- Without pooling: ~500ms-2s JVM startup per request
- With pooling: ~0ms startup (process reuse)
- Memory: Shared pool vs N separate JVM instances
"""

import asyncio
import logging
import os
import json
import subprocess
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from asyncio import Queue, Lock
from contextlib import asynccontextmanager
import time

logger = logging.getLogger(__name__)


@dataclass
class PooledProcess:
    """A pooled MCP server process."""
    process: subprocess.Popen
    process_id: int
    created_at: float
    request_count: int = 0
    last_used_at: float = 0
    is_busy: bool = False
    
    def is_healthy(self) -> bool:
        """Check if process is still running."""
        return self.process.poll() is None
    
    def should_recycle(self, max_requests: int, max_age_seconds: int) -> bool:
        """Check if process should be recycled."""
        if self.request_count >= max_requests:
            return True
        if time.time() - self.created_at > max_age_seconds:
            return True
        return False


class McpProcessPool:
    """
    Process pool for MCP servers.
    
    Maintains a pool of pre-warmed JVM processes running MCP servers.
    This dramatically reduces latency by avoiding JVM startup on each request.
    
    Configuration (environment variables):
    - MCP_POOL_SIZE: Number of processes in pool (default: 5)
    - MCP_POOL_MAX_REQUESTS: Max requests per process before recycle (default: 100)
    - MCP_POOL_MAX_AGE: Max process age in seconds (default: 3600)
    - MCP_POOL_ACQUIRE_TIMEOUT: Timeout for acquiring process (default: 30s)
    """
    
    def __init__(
        self,
        jar_path: str,
        pool_size: int = None,
        max_requests_per_process: int = None,
        max_process_age_seconds: int = None
    ):
        self.jar_path = jar_path
        self.pool_size = pool_size or int(os.environ.get("MCP_POOL_SIZE", "5"))
        self.max_requests = max_requests_per_process or int(os.environ.get("MCP_POOL_MAX_REQUESTS", "100"))
        self.max_age = max_process_age_seconds or int(os.environ.get("MCP_POOL_MAX_AGE", "3600"))
        self.acquire_timeout = int(os.environ.get("MCP_POOL_ACQUIRE_TIMEOUT", "30"))
        
        self._pool: List[PooledProcess] = []
        self._available: Queue = Queue()
        self._lock = Lock()
        self._initialized = False
        self._shutting_down = False
        
        # Metrics
        self._total_requests = 0
        self._cache_hits = 0
        self._process_recycles = 0
        self._errors = 0
    
    async def initialize(self, jvm_props: Dict[str, str] = None):
        """Initialize the process pool with pre-warmed processes."""
        if self._initialized:
            return
            
        async with self._lock:
            if self._initialized:
                return
                
            logger.info(f"Initializing MCP process pool with {self.pool_size} processes")
            
            for i in range(self.pool_size):
                try:
                    process = await self._create_process(jvm_props)
                    self._pool.append(process)
                    await self._available.put(process)
                    logger.debug(f"Created pooled process {i+1}/{self.pool_size}")
                except Exception as e:
                    logger.error(f"Failed to create pooled process {i+1}: {e}")
            
            self._initialized = True
            logger.info(f"MCP process pool initialized with {len(self._pool)} processes")
    
    async def _create_process(self, jvm_props: Dict[str, str] = None) -> PooledProcess:
        """Create a new MCP server process."""
        jvm_props = jvm_props or {}
        jvm_args = []
        
        for key, value in jvm_props.items():
            sanitized = str(value).replace("\n", " ")
            jvm_args.append(f"-D{key}={sanitized}")
        
        cmd = ["java"] + jvm_args + ["-jar", self.jar_path]
        
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0
        )
        
        # Wait a bit for JVM to warm up
        await asyncio.sleep(0.5)
        
        if process.poll() is not None:
            stderr = process.stderr.read().decode() if process.stderr else ""
            raise RuntimeError(f"MCP process failed to start: {stderr}")
        
        return PooledProcess(
            process=process,
            process_id=process.pid,
            created_at=time.time(),
            last_used_at=time.time()
        )
    
    @asynccontextmanager
    async def acquire(self):
        """
        Acquire a process from the pool.
        
        Usage:
            async with pool.acquire() as process:
                result = await send_request(process, ...)
        """
        process = None
        try:
            # Wait for available process with timeout
            try:
                process = await asyncio.wait_for(
                    self._available.get(),
                    timeout=self.acquire_timeout
                )
            except asyncio.TimeoutError:
                raise RuntimeError(f"Timeout acquiring MCP process after {self.acquire_timeout}s")
            
            # Check if process is healthy
            if not process.is_healthy():
                logger.warning(f"Acquired dead process {process.process_id}, creating new one")
                process = await self._replace_process(process)
            
            # Check if process should be recycled
            if process.should_recycle(self.max_requests, self.max_age):
                logger.debug(f"Recycling process {process.process_id} after {process.request_count} requests")
                process = await self._replace_process(process)
                self._process_recycles += 1
            
            process.is_busy = True
            yield process
            
            process.request_count += 1
            process.last_used_at = time.time()
            self._total_requests += 1
            
        except Exception as e:
            self._errors += 1
            if process and not process.is_healthy():
                # Process died, create replacement
                try:
                    process = await self._replace_process(process)
                except Exception as replace_error:
                    logger.error(f"Failed to replace dead process: {replace_error}")
            raise
        finally:
            if process:
                process.is_busy = False
                if not self._shutting_down:
                    await self._available.put(process)
    
    async def _replace_process(self, old_process: PooledProcess) -> PooledProcess:
        """Replace a process in the pool."""
        # Kill old process
        try:
            old_process.process.terminate()
            old_process.process.wait(timeout=5)
        except Exception:
            try:
                old_process.process.kill()
            except Exception:
                pass
        
        # Remove from pool
        if old_process in self._pool:
            self._pool.remove(old_process)
        
        # Create new process
        new_process = await self._create_process()
        self._pool.append(new_process)
        
        return new_process
    
    async def shutdown(self):
        """Shutdown all processes in the pool."""
        self._shutting_down = True
        
        logger.info("Shutting down MCP process pool")
        
        for process in self._pool:
            try:
                process.process.terminate()
                process.process.wait(timeout=5)
            except Exception:
                try:
                    process.process.kill()
                except Exception:
                    pass
        
        self._pool.clear()
        self._initialized = False
        
        logger.info("MCP process pool shutdown complete")
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get pool metrics."""
        active_count = sum(1 for p in self._pool if p.is_busy)
        healthy_count = sum(1 for p in self._pool if p.is_healthy())
        
        return {
            "pool_size": self.pool_size,
            "active_processes": active_count,
            "healthy_processes": healthy_count,
            "total_requests": self._total_requests,
            "process_recycles": self._process_recycles,
            "errors": self._errors,
            "processes": [
                {
                    "pid": p.process_id,
                    "request_count": p.request_count,
                    "age_seconds": int(time.time() - p.created_at),
                    "is_busy": p.is_busy,
                    "is_healthy": p.is_healthy()
                }
                for p in self._pool
            ]
        }


# Global pool instance (singleton pattern)
_global_pool: Optional[McpProcessPool] = None
_pool_lock = asyncio.Lock()


async def get_mcp_pool(jar_path: str = None) -> McpProcessPool:
    """
    Get or create the global MCP process pool.
    
    Thread-safe singleton pattern for sharing pool across requests.
    """
    global _global_pool
    
    if _global_pool is not None:
        return _global_pool
    
    async with _pool_lock:
        if _global_pool is not None:
            return _global_pool
        
        if jar_path is None:
            jar_path = os.environ.get(
                "MCP_SERVER_JAR",
                "/app/codecrow-mcp-servers-1.0.jar"
            )
        
        _global_pool = McpProcessPool(jar_path)
        await _global_pool.initialize()
        
        return _global_pool


async def shutdown_pool():
    """Shutdown the global pool on application exit."""
    global _global_pool
    if _global_pool:
        await _global_pool.shutdown()
        _global_pool = None
