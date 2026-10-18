"""No monoliths: source files stay under 400 lines and functions under 100.

Files and functions that were already too big are listed in ALLOWED with their size at the
time. They may shrink but never grow; when one gets under the limit, delete its entry.
ALLOWED must end up empty (see the refactor plan).
"""
import ast
from pathlib import Path
import re

ROOT = Path(__file__).parents[1]
MAX_FILE_LINES = 400
MAX_FUNCTION_LINES = 100
SOURCES = ("app/**/*.py", "web/*.html", "web/assets/*.js", "web/assets/*.css")

# Files or functions allowed to exceed the limits, with their size: they may only shrink.
# Empty since the no-monoliths refactor; keep it that way.
ALLOWED: dict[str, int] = {}

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


def test_no_file_or_function_is_too_big():
    too_big = []
    for key, lines, limit in measurements():
        allowed = ALLOWED.get(key, limit)
        if lines > allowed:
            too_big.append(f"{key}: {lines} lines (limit {allowed})")
    assert not too_big, "Split these into smaller, named pieces:\n" + "\n".join(too_big)


def test_allowed_entries_still_exist_and_are_still_too_big():
    measured = {key: (lines, limit) for key, lines, limit in measurements()}
    stale = [key for key in ALLOWED if key not in measured or measured[key][0] <= measured[key][1]]
    assert not stale, f"Remove these from ALLOWED (gone or now within limits): {stale}"


def test_the_size_check_sees_the_whole_app():
    keys = {key for key, _, _ in measurements()}
    assert {"app/main.py", "web/index.html", "web/assets/app-boot.js", "web/assets/tracks.css"} <= keys
    assert any(key.startswith("web/assets/tonie-library-render.js:") for key in keys), "JS functions are measured"
    assert any(key.startswith("app/routes/setup.py:") for key in keys), "Python functions are measured"
