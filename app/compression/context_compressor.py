"""
Context Compressor — transparent compression of agent multi-turn conversations.
"""
import copy
from dataclasses import dataclass
from typing import List, Tuple


@dataclass
class CompressionStats:
    original_chars: int
    compressed_chars: int
    savings_ratio: float  # 0.0 ~ 1.0

    @staticmethod
    def empty() -> "CompressionStats":
        return CompressionStats(0, 0, 0.0)


class ContextCompressor:
    """Compresses agent context to reduce token costs."""

    def __init__(
        self,
        tool_result_max_chars: int = 2000,
        tool_result_head_chars: int = 500,
        tool_result_tail_chars: int = 500,
        fold_after_turns: int = 6,
        fold_min_length: int = 200,
        fold_summary_length: int = 150,
    ):
        self.tool_result_max_chars = tool_result_max_chars
        self.tool_result_head_chars = tool_result_head_chars
        self.tool_result_tail_chars = tool_result_tail_chars
        self.fold_after_turns = fold_after_turns
        self.fold_min_length = fold_min_length
        self.fold_summary_length = fold_summary_length

    def compress(
        self, messages: List[dict], strategy: str
    ) -> Tuple[List[dict], CompressionStats]:
        """Compress messages according to strategy."""
        if strategy == "off":
            total = self._count_chars(messages)
            return messages, CompressionStats(total, total, 0.0)

        original_chars = self._count_chars(messages)
        result = copy.deepcopy(messages)

        # Tool result truncation (all strategies except off)
        result = self._truncate_tool_results(result)

        # History folding (moderate and aggressive only)
        if strategy in ("moderate", "aggressive"):
            result = self._fold_history(result)

        compressed_chars = self._count_chars(result)
        ratio = 1.0 - (compressed_chars / original_chars) if original_chars > 0 else 0.0
        return result, CompressionStats(original_chars, compressed_chars, ratio)

    def _truncate_tool_results(self, messages: List[dict]) -> List[dict]:
        result = []
        for msg in messages:
            if msg.get("role") == "user" and isinstance(msg.get("content"), list):
                new_content = []
                for block in msg["content"]:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        block = self._truncate_single(block)
                    new_content.append(block)
                result.append({**msg, "content": new_content})
            else:
                result.append(msg)
        return result

    def _truncate_single(self, block: dict) -> dict:
        # Skip blocks with cache_control to preserve prompt cache prefix
        if block.get("cache_control"):
            return block
        content = block.get("content", "")
        if isinstance(content, str) and len(content) > self.tool_result_max_chars:
            head = content[: self.tool_result_head_chars]
            tail = content[-self.tool_result_tail_chars :]
            marker = f"\n\n... [已省略 {len(content)} 字符中的 {len(content) - self.tool_result_head_chars - self.tool_result_tail_chars} 字符] ...\n\n"
            return {**block, "content": head + marker + tail}
        return block

    def _fold_history(self, messages: List[dict]) -> List[dict]:
        total = len(messages)
        keep_count = self.fold_after_turns * 2
        fold_boundary = total - keep_count
        if fold_boundary <= 0:
            return messages

        result = []
        for i, msg in enumerate(messages):
            if i < fold_boundary and msg.get("role") == "assistant":
                # Skip messages with cache_control to preserve prompt cache prefix
                if self._has_cache_control(msg):
                    result.append(msg)
                    continue
                text = self._extract_text(msg)
                if len(text) > self.fold_min_length:
                    summary = text[: self.fold_summary_length] + "..."
                    if isinstance(msg.get("content"), str):
                        result.append({**msg, "content": summary})
                    else:
                        result.append({**msg, "content": [{"type": "text", "text": summary}]})
                    continue
            result.append(msg)
        return result

    def _extract_text(self, msg: dict) -> str:
        content = msg.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return " ".join(
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
        return ""

    def _has_cache_control(self, msg: dict) -> bool:
        """Check if any block in the message has cache_control."""
        content = msg.get("content", "")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("cache_control"):
                    return True
        return False

    def _count_chars(self, messages: List[dict]) -> int:
        return sum(len(str(m)) for m in messages)
