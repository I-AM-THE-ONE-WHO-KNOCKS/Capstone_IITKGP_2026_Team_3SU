"""Console log for RiskSphere.

The level is read from settings: trace, debug, or error. Ranks are
error=1, trace=2, debug=3. A message is printed when the configured rank
is at least the message rank, so error shows failures only, trace adds the
flow, and debug adds tool JSON and the model prompt and reply.

Each line is timestamp:filename : line: message. The line number is the
call site, not a frame inside this module.
"""

import inspect
import json
import os
import sys
from datetime import datetime
from pathlib import Path

_RANK = {"error": 1, "trace": 2, "debug": 3}
_level = "trace"


def configure(level=None):
    """Set the log level for this process.

    level overrides the setting when it is passed. An unknown name is
    logged as an error and the level falls back to trace. Call once at
    startup. Later questions keep the level chosen here.
    """
    global _level
    if level is None:
        path = Path(__file__).resolve().parent / "config.json"
        try:
            level = json.loads(path.read_text()).get("log_level", "trace")
        except (OSError, json.JSONDecodeError):
            level = "trace"
    name = str(level or "trace").strip().lower()
    if name not in _RANK:
        _level = "trace"
        error(f"unknown log_level {level}, using trace")
        return
    _level = name


def _write(level, message):
    """Print one line if the configured level includes it.

    Walks the stack and skips frames in this module and the _say helper,
    so the printed line number is the caller in the desk code.
    """
    if _RANK[level] > _RANK[_level]:
        return
    frame = None
    for item in inspect.stack()[1:]:
        if Path(item.filename).name == "log.py" or item.function == "_say":
            continue
        frame = item
        break
    if frame is None:
        frame = inspect.stack()[1]
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    name = Path(frame.filename).name
    # The MCP server owns stdout for JSON-RPC. Its logs go to stderr.
    stream = sys.stderr if os.environ.get("MCP_STDIO") == "1" else sys.stdout
    print(f"{stamp}:{name} : {frame.lineno}: {message}", file=stream, flush=True)


def trace(message):
    """Flow line: a question step, a tool call, or a successful download."""
    _write("trace", message)


def debug(message):
    """Detail line: tool JSON, the model prompt, or the model reply.

    Shown only when the level is debug. Do not pass the API key.
    """
    _write("debug", message)


def error(message):
    """Failure line. Printed at every level, including error."""
    _write("error", message)
