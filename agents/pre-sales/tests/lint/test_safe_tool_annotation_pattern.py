"""Lint: ``@safe_tool``-decorated callables must not use PEP-563 deferred annotations.

Root cause (observed twice in PR 5 — once for ``Literal`` in
``assemble_payload.py``, once for ``ToolContext`` in
``revision/tools.py`` / ``revision/log_tools.py``):

The ``@safe_tool`` decorator from ``app.shared.errors`` wraps each tool
in an async/sync wrapper using ``functools.wraps``. ``functools.wraps``
copies ``__annotations__``, ``__doc__``, ``__name__``, ``__qualname__``,
``__module__``, and ``__wrapped__`` from the original function — but
NOT ``__globals__`` (which is a read-only attribute tied to the code
object's defining module).

When ADK introspects a tool to build the Gemini function declaration it
calls ``typing.get_type_hints(wrapper_func)`` (see
``google/adk/tools/_automatic_function_calling_util.py``). With
``from __future__ import annotations`` (PEP 563) in the tool's source
file, every annotation is stored as a *string*; ``get_type_hints`` then
tries to resolve those strings against ``wrapper.__globals__`` — which
is ``app.shared.errors.__globals__``, where ``Literal``, ``ToolContext``,
and friends are not imported. The first call to the tool dies with
``NameError: name 'X' is not defined``.

Workaround: keep PEP-563 deferred annotations OUT of any module that
defines a ``@safe_tool`` function. Without the future import,
annotations are evaluated at definition time and the resolved type
objects sit directly in ``__annotations__`` — no globals lookup needed.

This lint scans the ``app/`` package for the dangerous combination so
the regression cannot return silently.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


_APP_DIR = Path(__file__).resolve().parents[2] / 'app'

_FUTURE_PATTERN = re.compile(
    r'^\s*from\s+__future__\s+import\s+annotations\b',
    re.MULTILINE,
)
_SAFE_TOOL_PATTERN = re.compile(r'^\s*@safe_tool\b', re.MULTILINE)


def _python_modules() -> list[Path]:
    """All ``app/**/*.py`` excluding caches and dunder-init files."""
    return [
        p for p in _APP_DIR.rglob('*.py')
        if '__pycache__' not in p.parts
    ]


def _has_safe_tool_and_future_annotations(path: Path) -> bool:
    text = path.read_text(encoding='utf-8')
    return bool(
        _SAFE_TOOL_PATTERN.search(text)
        and _FUTURE_PATTERN.search(text),
    )


# Computed at import time so pytest can enumerate one parametrized case per
# module — failure messages then name the offending file explicitly.
_MODULES = _python_modules()


@pytest.mark.parametrize('module_path', _MODULES, ids=lambda p: str(p.relative_to(_APP_DIR)))
def test_safe_tool_module_must_not_defer_annotations(module_path: Path) -> None:
    """Any module with ``@safe_tool`` MUST NOT use ``from __future__ import annotations``."""
    if not _has_safe_tool_and_future_annotations(module_path):
        return  # not the dangerous combination — fine.

    rel = module_path.relative_to(_APP_DIR)
    pytest.fail(
        f'{rel} combines @safe_tool with `from __future__ import annotations`. '
        'This breaks ADK function-declaration generation: typing.get_type_hints '
        "on the @safe_tool wrapper resolves string annotations against the "
        "wrapper's __globals__ (app.shared.errors), where the tool's own "
        "imports are NOT visible — first invocation raises NameError. "
        'Remove the `from __future__ import annotations` line and let '
        'Python evaluate the annotations eagerly.'
    )
