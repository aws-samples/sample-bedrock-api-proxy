"""
Helpers for `tool_choice` handling inside proxy-side agentic loops.

Server tools that Bedrock does not implement natively (web search, web fetch,
standalone code execution) are emulated by running a multi-turn loop against
Bedrock: the marker tool is replaced with a regular custom tool, and the proxy
executes the tool itself and feeds the result back until the model stops asking
for it.

The client's `tool_choice` describes what should happen on the *first* assistant
turn. Replaying a forcing choice on every continuation makes the model call the
intercepted tool again on every single turn, so the loop can never reach
`end_turn` and instead burns through `MAX_ITERATIONS`.
"""
from typing import Any, Dict, Optional, Union

ToolChoice = Optional[Union[str, Dict[str, Any]]]

# tool_choice variants that *force* the model to emit a tool call.
# "auto"/"none" leave the model free to answer, so they need no adjustment.
_FORCING_TYPES = frozenset({"any", "tool"})


def relax_forced_tool_choice(tool_choice: ToolChoice) -> ToolChoice:
    """
    Downgrade a forcing `tool_choice` to "auto" for agentic-loop continuations.

    Returns `tool_choice` unchanged when it is absent or already non-forcing
    ("auto"/"none"), preserving the shape the client used. `disable_parallel_
    tool_use` is carried over when present.

    Example: Claude Code (>= 2.1.2xx) sends
    `{"type": "tool", "name": "web_search"}` on the internal query behind its
    WebSearch tool. Without this relaxation the model is forced to search on
    every continuation, exhausts `max_uses`, and then keeps being forced while
    receiving `max_uses_exceeded` errors until the iteration cap is hit.
    """
    if not tool_choice:
        return tool_choice

    if isinstance(tool_choice, str):
        return "auto" if tool_choice in _FORCING_TYPES else tool_choice

    if isinstance(tool_choice, dict):
        if tool_choice.get("type") not in _FORCING_TYPES:
            return tool_choice
        relaxed: Dict[str, Any] = {"type": "auto"}
        no_parallel = tool_choice.get("disable_parallel_tool_use")
        if no_parallel is not None:
            relaxed["disable_parallel_tool_use"] = no_parallel
        return relaxed

    return tool_choice
