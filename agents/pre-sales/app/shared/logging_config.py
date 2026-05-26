from __future__ import annotations

import logging
import os
from pathlib import Path

import structlog


# Verbose telemetry envelope for the SOW quality-loop debug trail.
#
# When enabled, three additional layers of logging fire:
#
# - ``quality_loop_manifest_snapshot`` — full Extraction Manifest at
#   loop start (one event per session).
# - ``quality_loop_sow_snapshot_pre_round`` / ``..._post_round`` — full
#   staged SOW before and after every round (two events per round).
# - ``section_patch_ops_executed.ops[].payload`` — actual content the
#   worker is writing (``fields`` / ``value`` / ``item``), not just the
#   op kind + target. This is the smoking-gun trail when a finding
#   persists across rounds and we need to see whether the worker's
#   patch was substantive vs off-target.
#
# Trade-off: a verbose session adds ~200KB to the log. The structural
# telemetry (hashes, anchor diffs, refusal events, drift summary)
# stays on regardless because it is small and load-bearing for the
# everyday debugging signals.
#
# Activate via env var:
#
#   SOW_VERBOSE_LOGGING=1 adk web        # full forensic trail
#   adk web                              # default — structural only
#
# Honors ``1``, ``true``, ``yes`` (case-insensitive). Anything else
# (unset, ``0``, ``false``) keeps the default off.
_VERBOSE_SOW_LOGGING_ENV = 'SOW_VERBOSE_LOGGING'
_VERBOSE_TRUTHY_VALUES = frozenset({'1', 'true', 'yes', 'on'})


def is_verbose_sow_logging() -> bool:
    """Return True when the verbose SOW telemetry mode is active.

    Reads the env var on every call so a developer toggling the var
    between test invocations does not need to restart the process.
    Cost is a dict lookup; the result is not cached.
    """
    raw = os.environ.get(_VERBOSE_SOW_LOGGING_ENV, '').strip().lower()
    return raw in _VERBOSE_TRUTHY_VALUES


def setup_logging(
    level: str = 'INFO',
    json_output: bool = True,
    log_file: str | None = None,
) -> None:
    """Configure structlog for Cloud Run / Agent Engine.

    - JSON output for production (Cloud Logging compatible).
    - Console output for local development.
    - Context binding (user_id, tool, session propagate automatically).
    - Optional file mirror (``log_file``) so logs survive terminal
      truncation during long local sessions. The file always uses the
      JSON renderer regardless of ``json_output`` so the on-disk format
      is grep/jq-friendly; the console keeps whichever renderer was
      chosen for interactive use.
    """
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt='iso'),
        structlog.stdlib.add_logger_name,
    ]

    if json_output:
        console_renderer = structlog.processors.JSONRenderer()
    else:
        console_renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level),
        ),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
    )

    console_formatter = structlog.stdlib.ProcessorFormatter(
        processors=[*shared_processors, console_renderer],
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(console_formatter)
    handlers: list[logging.Handler] = [console_handler]

    if log_file:
        # Ensure the parent directory exists so the FileHandler does not
        # crash on first write. The handler appends — restarting the
        # agent keeps the previous run's logs intact.
        log_path = Path(log_file).expanduser().resolve()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding='utf-8')
        file_handler.setFormatter(
            structlog.stdlib.ProcessorFormatter(
                processors=[
                    *shared_processors,
                    structlog.processors.JSONRenderer(),
                ],
            ),
        )
        handlers.append(file_handler)

    root = logging.getLogger()
    root.handlers = handlers
    root.setLevel(getattr(logging, level))
