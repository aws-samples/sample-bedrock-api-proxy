"""In-memory cache for beta header configuration from DynamoDB."""
import logging
import threading
from typing import Dict, List, Optional, Set

from app.core.config import settings

logger = logging.getLogger(__name__)


class BetaHeaderConfigCache:
    """Thread-safe in-memory cache for beta header rules.

    Loads from DynamoDB at startup, refreshes every refresh_interval seconds.
    Falls back to config.py defaults if DynamoDB is empty or unreachable.
    """

    _instance: Optional["BetaHeaderConfigCache"] = None
    _lock = threading.Lock()

    def __init__(self, refresh_interval: int = 300):
        self._refresh_interval = refresh_interval
        self._blocklist: Set[str] = set()
        self._mapping: Dict[str, List[str]] = {}
        self._data_lock = threading.Lock()
        self._timer: Optional[threading.Timer] = None
        self._loaded = False

    @classmethod
    def instance(cls, refresh_interval: int = 300) -> "BetaHeaderConfigCache":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(refresh_interval)
                    cls._instance.start()
        return cls._instance

    def start(self):
        """Load data and start periodic refresh."""
        from app.db.dynamodb import DynamoDBClient, BetaHeaderManager
        self._manager = BetaHeaderManager(DynamoDBClient())
        self._refresh()
        self._schedule_next()

    def stop(self):
        """Stop periodic refresh."""
        if self._timer:
            self._timer.cancel()
            self._timer = None

    def _schedule_next(self):
        self._timer = threading.Timer(self._refresh_interval, self._refresh_and_reschedule)
        self._timer.daemon = True
        self._timer.start()

    def _refresh_and_reschedule(self):
        self._refresh()
        self._schedule_next()

    def _refresh(self):
        """Reload data from DynamoDB, fall back to config defaults."""
        try:
            items = self._manager.list_all()

            if items:
                blocklist = set()
                mapping = {}
                for item in items:
                    name = item["header_name"]
                    htype = item["header_type"]
                    if htype == "blocklist":
                        blocklist.add(name)
                    elif htype == "mapping":
                        mapping[name] = item.get("mapped_to", [])

                with self._data_lock:
                    self._blocklist = blocklist
                    self._mapping = mapping
                    self._loaded = True
                logger.info(f"Beta header cache refreshed: {len(blocklist)} blocklist, {len(mapping)} mapping")
            else:
                self._load_defaults()
        except Exception as e:
            logger.warning(f"Failed to load beta headers from DynamoDB, using defaults: {e}")
            if not self._loaded:
                self._load_defaults()

    def _load_defaults(self):
        """Load fallback defaults from config.py."""
        with self._data_lock:
            self._blocklist = set(settings.beta_headers_blocklist)
            self._mapping = dict(settings.beta_header_mapping)
            self._loaded = True
        logger.info("Beta header cache loaded from config defaults")

    def get_blocklist(self) -> Set[str]:
        with self._data_lock:
            return set(self._blocklist)

    def get_mapping(self) -> Dict[str, List[str]]:
        with self._data_lock:
            return dict(self._mapping)
