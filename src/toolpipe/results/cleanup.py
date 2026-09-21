"""Expired-result cleanup: startup + periodic asyncio task."""

from __future__ import annotations

import asyncio
import logging

from toolpipe.results.manager import ResultManager

logger = logging.getLogger(__name__)
CLEANUP_INTERVAL_SECONDS = 60


def run_startup_cleanup(manager: ResultManager) -> int:
    count = manager.cleanup_expired()
    if count:
        logger.info("startup cleanup removed %d expired results", count)
    return count


async def periodic_cleanup(
    manager: ResultManager, interval: int = CLEANUP_INTERVAL_SECONDS
) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            count = manager.cleanup_expired()
            if count:
                logger.info("periodic cleanup removed %d expired results", count)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("periodic cleanup failed")
