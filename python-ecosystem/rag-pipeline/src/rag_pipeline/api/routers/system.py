"""System endpoints — health, GC, memory."""
import gc
import logging
from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)
router = APIRouter(tags=["system"])


@router.get("/")
def root():
    return {"message": "CodeCrow RAG Pipeline API", "version": "2.0.0"}


@router.get("/health")
def health():
    return {"status": "healthy"}


@router.post("/system/gc")
def force_garbage_collection():
    """Force garbage collection to free memory."""
    try:
        import psutil
        process = psutil.Process()
        memory_before = process.memory_info().rss / 1024 / 1024

        collected = gc.collect()

        memory_after = process.memory_info().rss / 1024 / 1024
        freed = memory_before - memory_after

        logger.info(f"Garbage collection: collected {collected} objects, freed {freed:.2f} MB")

        return {
            "status": "ok",
            "objects_collected": collected,
            "memory_before_mb": round(memory_before, 2),
            "memory_after_mb": round(memory_after, 2),
            "memory_freed_mb": round(freed, 2)
        }
    except ImportError:
        collected = gc.collect()
        return {"status": "ok", "objects_collected": collected}
    except Exception as e:
        logger.error(f"Error during garbage collection: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/system/memory")
def get_memory_usage():
    """Get current memory usage."""
    try:
        import psutil
        process = psutil.Process()
        memory_info = process.memory_info()

        return {
            "rss_mb": round(memory_info.rss / 1024 / 1024, 2),
            "vms_mb": round(memory_info.vms / 1024 / 1024, 2),
            "percent": round(process.memory_percent(), 2)
        }
    except ImportError:
        return {"error": "psutil not installed"}
    except Exception as e:
        logger.error(f"Error getting memory info: {e}")
        raise HTTPException(status_code=500, detail=str(e))
