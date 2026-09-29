"""Ordered review work whose child operations share the parent's lifetime."""
from __future__ import annotations

import asyncio
from typing import Awaitable, TypeVar

_Result = TypeVar("_Result")


async def gather_review_work(*operations: Awaitable[_Result]) -> list[_Result]:
    """Preserve input order and join every child if any operation aborts."""
    tasks = [asyncio.ensure_future(operation) for operation in operations]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
