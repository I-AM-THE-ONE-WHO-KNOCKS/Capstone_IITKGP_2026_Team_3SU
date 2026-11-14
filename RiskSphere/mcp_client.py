"""MCP client used by the RiskSphere console.

Starts one local tool server for the session and keeps it up, so the
future-exposure checks are not repeated on every question. Tool calls are
tools/call. reset() sends notifications/risksphere/reset_book so the next
question reloads prices.

stdout of the server is the protocol. Its logs stay on stderr and show up
in this console.
"""

import atexit
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROTOCOL = "2024-11-05"

_proc = None
_next_id = 0
_stopped = False


def _write(payload):
    if _proc is None or _proc.poll() is not None or _proc.stdin is None:
        raise RuntimeError("The calculation server is not running.")
    _proc.stdin.write(json.dumps(payload, ensure_ascii=True) + "\n")
    _proc.stdin.flush()


def _read(expect_id):
    if _proc is None or _proc.stdout is None:
        raise RuntimeError("The calculation server is not running.")
    while True:
        line = _proc.stdout.readline()
        if line == "":
            raise RuntimeError("The calculation server stopped before it replied.")
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError("The calculation server sent a line that is not JSON.") from exc
        if payload.get("id") != expect_id:
            continue
        if "error" in payload:
            err = payload["error"] or {}
            raise RuntimeError(err.get("message") or "The calculation server returned an error.")
        return payload.get("result") or {}


def _request(method, params):
    global _next_id
    _next_id += 1
    msg = {"jsonrpc": "2.0", "id": _next_id, "method": method, "params": params}
    _write(msg)
    return _read(_next_id)


def start():
    """Start the tool server and complete the MCP initialize handshake.

    A second call does nothing while the process is still alive.
    """
    global _proc
    if _proc is not None and _proc.poll() is None:
        return
    env = os.environ.copy()
    env["MCP_STDIO"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    _proc = subprocess.Popen(
        [sys.executable, "-u", str(ROOT / "mcp_server.py")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=None,
        env=env,
        cwd=str(ROOT),
        text=True,
        bufsize=1,
    )
    result = _request("initialize", {
        "protocolVersion": PROTOCOL,
        "capabilities": {},
        "clientInfo": {"name": "RiskSphere", "version": "1.0.0"},
    })
    version = result.get("protocolVersion")
    if version != PROTOCOL:
        raise RuntimeError(f"The calculation server speaks protocol {version}.")
    _write({"jsonrpc": "2.0", "method": "notifications/initialized"})


def reset():
    """Ask the server to drop its cached book before the tools for this question."""
    start()
    _write({"jsonrpc": "2.0", "method": "notifications/risksphere/reset_book"})


def call_tool(name, arguments):
    """Run one calculation through tools/call. Returns the tool dict.

    name is the tool name. arguments is the object from the plan.
    A tool failure is raised as RuntimeError. The protocol call itself
    succeeded in that case. The server is marked isError.
    """
    start()
    result = _request("tools/call", {"name": name, "arguments": arguments or {}})
    if result.get("isError"):
        content = result.get("content") or []
        text = content[0].get("text") if content else ""
        raise RuntimeError(text or f"{name} failed")
    if isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"]
    content = result.get("content") or []
    if content and content[0].get("text"):
        return json.loads(content[0]["text"])
    raise RuntimeError(f"{name} returned no result")


def stop():
    """Close the server. Safe to call more than once."""
    global _proc, _stopped
    if _stopped:
        return
    _stopped = True
    proc = _proc
    _proc = None
    if proc is None or proc.poll() is not None:
        return
    if proc.stdin is not None:
        proc.stdin.close()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()


atexit.register(stop)
