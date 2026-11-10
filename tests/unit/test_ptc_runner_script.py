"""The generated PTC sandbox runner must embed tool definitions safely."""

import ast
import json

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


def _tools_info_literal(src: str) -> str:
    """Return the string literal passed to json.loads() in `TOOLS_INFO = ...`.

    Inspects the generated source with ast only; nothing is executed.
    """
    for node in ast.parse(src).body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "TOOLS_INFO" for t in node.targets)
        ):
            call = node.value
            assert isinstance(call, ast.Call)
            assert isinstance(call.func, ast.Attribute) and call.func.attr == "loads"
            assert isinstance(call.func.value, ast.Name) and call.func.value.id == "json"
            (arg,) = call.args
            assert isinstance(arg, ast.Constant) and isinstance(arg.value, str)
            return arg.value
    raise AssertionError("TOOLS_INFO assignment not found")


def test_tools_info_round_trips_json_booleans_null_and_quotes():
    assert json.loads(_tools_info_literal(_runner(TOOLS))) == TOOLS


def test_tool_text_cannot_inject_code():
    evil = [{"name": "x", "description": "'''\nimport os; os.system('id')\n'''", "input_schema": {}}]
    tree = ast.parse(_runner(evil))
    # The only top-level os.system call would come from injected text.
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "system"
    ]
    assert calls == []
