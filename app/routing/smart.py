"""
Smart Router — RouteLLM integration for complexity-based model selection.
"""
import logging

logger = logging.getLogger(__name__)


class SmartRouter:
    """Lazy-loads RouteLLM to classify query complexity."""

    def __init__(self, strong_model: str, weak_model: str, threshold: float = 0.5):
        self.strong_model = strong_model
        self.weak_model = weak_model
        self.threshold = threshold
        self._router = None

    def _ensure_loaded(self) -> None:
        if self._router is None:
            try:
                from routellm.controller import Controller
                self._router = Controller(
                    routers=["mf"],
                    strong_model=self.strong_model,
                    weak_model=self.weak_model,
                )
            except ImportError:
                logger.warning("routellm not installed, smart routing unavailable")
                self._router = "unavailable"

    def classify(self, user_message: str) -> str:
        """Return 'high' or 'low' complexity."""
        self._ensure_loaded()
        if self._router == "unavailable":
            return "high"
        try:
            result = self._router.completion(
                model=f"router-mf-{self.threshold}",
                messages=[{"role": "user", "content": user_message}],
            )
            chosen = getattr(result, "model", "")
            return "high" if chosen == self.strong_model else "low"
        except Exception as e:
            logger.warning("SmartRouter classification failed: %s", e)
            return "high"
