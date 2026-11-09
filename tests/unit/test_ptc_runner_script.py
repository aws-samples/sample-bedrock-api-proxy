"""The generated PTC sandbox runner must embed tool definitions safely."""

import ast

from app.services.ptc.sandbox import PTCSandboxExecutor

TOOLS = [
    {
        "name": "lookup",
        "description": "Has ''' triple quotes, \"quotes\", a backslash \\ and {braces}",
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"q": {"type": "string", "default": None}},
            "required": ["q"],
            "x-flag": True,
        },
    }
]


def _runner(tools):
    return PTCSandboxExecutor._get_runner_script(object(), tools, loop_mode=True)


def test_runner_script_is_valid_python():
    ast.parse(_runner(TOOLS))


def test_tools_info_round_trips_json_booleans_null_and_quotes():
    src = _runner(TOOLS)
    line = next(l for l in src.splitlines() if l.startswith("TOOLS_INFO"))
    namespace: dict = {}
    exec("import json\n" + line, namespace)  # noqa: S102 - evaluating our own generated literal
    assert namespace["TOOLS_INFO"] == TOOLS


def test_tool_text_cannot_inject_code():
    evil = [{"name": "x", "description": "'''\nimport os; os.system('id')\n'''", "input_schema": {}}]
    tree = ast.parse(_runner(evil))
    # The only top-level os.system call would come from injected text.
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "system"
    ]
    assert calls == []
