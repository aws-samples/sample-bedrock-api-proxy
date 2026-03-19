"""
Usage Aggregation Service.

Periodically aggregates token usage from the usage table into the usage_stats table.
"""
import asyncio
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from app.db.dynamodb import DynamoDBClient, APIKeyManager, UsageStatsManager, ModelPricingManager


class UsageAggregator:
    """Service to aggregate usage statistics periodically."""

    def __init__(self, interval_seconds: int = 300):
        """
        Initialize the usage aggregator.

        Args:
            interval_seconds: Interval between aggregation runs (default: 300 = 5 minutes)
        """
        self.interval_seconds = interval_seconds
        self._task: Optional[asyncio.Task] = None
        self._running = False

    def _get_managers(self):
        """Get DynamoDB managers."""
        db_client = DynamoDBClient()
        return (
            APIKeyManager(db_client),
            UsageStatsManager(db_client),
            ModelPricingManager(db_client),
        )

    def aggregate_usage(self) -> int:
        """
        Aggregate usage for all API keys.

        Returns:
            Number of keys aggregated
        """
        api_key_manager, usage_stats_manager, pricing_manager = self._get_managers()

        # Get all API keys with pagination
        api_keys = []
        last_key = None
        while True:
            result = api_key_manager.list_all_api_keys(limit=1000, last_key=last_key)
            api_keys.extend([item["api_key"] for item in result.get("items", [])])
            last_key = result.get("last_key")
            if not last_key:
                break

        # Aggregate usage for all keys with cost calculation
        count = usage_stats_manager.aggregate_all_usage(
            api_keys,
            pricing_manager=pricing_manager,
            api_key_manager=api_key_manager,
        )

        return count

    async def _run_aggregation_loop(self):
        """Run the aggregation loop."""
        while self._running:
            try:
                # Run aggregation in thread pool to avoid blocking
                count = await asyncio.get_event_loop().run_in_executor(
                    None, self.aggregate_usage
                )
                print(f"[UsageAggregator] Aggregated usage for {count} API keys")
            except Exception as e:
                print(f"[UsageAggregator] Error during aggregation: {e}")

            # Wait for the next interval
            await asyncio.sleep(self.interval_seconds)

    def start(self):
        """Start the aggregation background task."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._run_aggregation_loop())
        print(f"[UsageAggregator] Started with {self.interval_seconds}s interval")

    def stop(self):
        """Stop the aggregation background task."""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        print("[UsageAggregator] Stopped")


# Global instance
_aggregator: Optional[UsageAggregator] = None


def get_aggregator(interval_seconds: int = 300) -> UsageAggregator:
    """Get or create the global usage aggregator instance."""
    global _aggregator
    if _aggregator is None:
        _aggregator = UsageAggregator(interval_seconds=interval_seconds)
    return _aggregator


def start_aggregator(interval_seconds: int = 300):
    """Start the global usage aggregator."""
    aggregator = get_aggregator(interval_seconds)
    aggregator.start()


def stop_aggregator():
    """Stop the global usage aggregator."""
    global _aggregator
    if _aggregator:
        _aggregator.stop()
        _aggregator = None
