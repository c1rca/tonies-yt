"""No monoliths: source files stay under 400 lines and functions under 100.

Files and functions that were already too big are listed in ALLOWED with their size at the
time. They may shrink but never grow; when one gets under the limit, delete its entry.
ALLOWED must end up empty (see the refactor plan).
"""
import ast
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).parents[1]
MAX_FILE_LINES = 400
MAX_FUNCTION_LINES = 100
SOURCES = ("app/**/*.py", "web/*.html", "web/assets/*.js", "web/assets/*.css")

# Each entry has its owner task's comment above it. When your file or function gets under
# the limit, delete only the entry line (keep the comment line, so parallel edits never touch
# neighbouring lines). The integrator tidies the comments.
ALLOWED = {
    # T3
    "web/assets/tonie-library-render.js:renderToniesContent": 365,
    # T8
    "web/assets/workspace.css": 1516,
    # T9
    # T10
    "app/main.py": 619,
    # T11
}

_JS_FUNCTION = re.compile(r"^(?:async\s+)?function\s+(\w+)")


def _source_files():
    return sorted({p for pattern in SOURCES for p in ROOT.glob(pattern) if "__pycache__" not in p.parts})


def _python_functions(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node.name, node.end_lineno - node.lineno + 1


def _js_functions(path: Path):
    """Top-level functions: from `function name(` to its closing `}` alone at the start of a line."""
    lines = path.read_text(encoding="utf-8").split("\n")
    for start, line in enumerate(lines):
        match = _JS_FUNCTION.match(line)
        if match:
            end = next((i for i in range(start, len(lines)) if lines[i].rstrip() == "}"), len(lines) - 1)
            yield match.group(1), end - start + 1


def measurements():
    for path in _source_files():
        rel = path.relative_to(ROOT).as_posix()
        yield rel, len(path.read_text(encoding="utf-8").splitlines()), MAX_FILE_LINES
        functions = _python_functions(path) if path.suffix == ".py" else _js_functions(path) if path.suffix == ".js" else ()
        for name, lines in functions:
            yield f"{rel}:{name}", lines, MAX_FUNCTION_LINES


@pytest.mark.parametrize("key,lines,limit", [m for m in measurements() if m[1] > m[2] or m[0] in ALLOWED],
                         ids=lambda v: v if isinstance(v, str) else "")
def test_size_limits(key, lines, limit):
    if key in ALLOWED:
        assert lines <= ALLOWED[key], f"{key} grew to {lines} lines (was {ALLOWED[key]}); split it instead"
    else:
        assert lines <= limit, f"{key} is {lines} lines; the limit is {limit}. Split it into named pieces."


def test_allowed_entries_still_exist_and_are_still_too_big():
    measured = {key: (lines, limit) for key, lines, limit in measurements()}
    stale = [key for key in ALLOWED if key not in measured or measured[key][0] <= measured[key][1]]
    assert not stale, f"Remove these from ALLOWED (gone or now within limits): {stale}"
