"""
Periodic Model Pricing Sync Service.

Periodically pulls the LiteLLM price table and syncs it into the model
pricing table. Enabled via PRICING_SYNC_ENABLED; interval via
PRICING_SYNC_INTERVAL_HOURS.
"""

import asyncio
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from app.core.config import settings
from app.services.pricing_sync_service import run_sync


class PricingSyncScheduler:
    """Service to sync model pricing periodically."""

    def __init__(self, interval_seconds: float):
        self.interval_seconds = interval_seconds
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def _run_sync_loop(self):
        while self._running:
            try:
                # run_sync is blocking (httpx + boto3); run in thread pool
                summary = await asyncio.get_event_loop().run_in_executor(None, run_sync)
                print(
                    f"[PricingSync] Synced from {summary['source_url']}: "
                    f"{len(summary['created'])} created, {len(summary['updated'])} updated, "
                    f"{summary['unchanged']} unchanged, "
                    f"{len(summary['skipped_manual'])} manual rows skipped"
                )
            except Exception as e:
                print(f"[PricingSync] Error during sync: {e}")

            await asyncio.sleep(self.interval_seconds)

    def start(self):
        """Start the sync background task."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._run_sync_loop())
        print(f"[PricingSync] Started with {self.interval_seconds}s interval")

    def stop(self):
        """Stop the sync background task."""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        print("[PricingSync] Stopped")


# Global instance
_scheduler: Optional[PricingSyncScheduler] = None


def start_pricing_sync():
    """Start the periodic pricing sync if PRICING_SYNC_ENABLED is set."""
    global _scheduler
    if not settings.pricing_sync_enabled:
        print("[PricingSync] Disabled (set PRICING_SYNC_ENABLED=True to enable)")
        return
    if _scheduler is None:
        _scheduler = PricingSyncScheduler(
            interval_seconds=settings.pricing_sync_interval_hours * 3600
        )
    _scheduler.start()


def stop_pricing_sync():
    """Stop the periodic pricing sync."""
    global _scheduler
    if _scheduler:
        _scheduler.stop()
        _scheduler = None
