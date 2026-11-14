"""MCP server for the RiskSphere calculations.

The console starts this process and speaks JSON-RPC 2.0 on stdin and stdout,
one JSON object per line. That is the stdio transport in the Model Context
Protocol. Logs go to stderr so a log line cannot be read as a protocol message.

The methods used are initialize, notifications/initialized, tools/list,
tools/call, and ping. notifications/risksphere/reset_book drops the cached
book before the next question. The five tools are the same calculations the
planner already names. This process runs the Python. It does not call Qwen.
"""

import json
import sys

import log
import tools

PROTOCOL = "2024-11-05"

TOOLS = [
    {
        "name": "current_exposure",
        "description": (
            "Today's exposure for one counterparty against the stored limit. "
            "Uses the latest Yahoo close. Does not simulate future exposure."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "counterparty": {
                    "type": "string",
                    "description": "Id or name: meridian, apex, or harbor.",
                }
            },
            "required": ["counterparty"],
            "additionalProperties": False,
        },
    },
    {
        "name": "calculate_margin_call",
        "description": (
            "Variation margin for one counterparty from the stored agreement. "
            "Does not send or book the call."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "counterparty": {
                    "type": "string",
                    "description": "Id or name: meridian, apex, or harbor.",
                }
            },
            "required": ["counterparty"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_market_data",
        "description": "Last Yahoo close and one-year volatility for one ticker.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Yahoo symbol, for example SPY or EURUSD=X.",
                }
            },
            "required": ["ticker"],
            "additionalProperties": False,
        },
    },
    {
        "name": "regulatory_haircut",
        "description": "One cell of the standardised haircut schedule, in percent. No market download.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "asset_class": {
                    "type": "string",
                    "description": "cash, government, corporate, equity_main_index, equity_other, or gold.",
                },
                "maturity_bucket": {
                    "type": "string",
                    "description": "<=1y, 1-5y, or >5y. Bonds only.",
                },
                "rating_bucket": {
                    "type": "string",
                    "description": "AAA-AA- or A+-BBB-. Bonds only.",
                },
            },
            "required": ["asset_class"],
            "additionalProperties": False,
        },
    },
    {
        "name": "check_pfe_limit",
        "description": (
            "Simulated potential future exposure for one counterparty against the stored limit. "
            "Runs the engine checks first. The paths use variation margin, not posted cash."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "counterparty": {
                    "type": "string",
                    "description": "Id or name: meridian, apex, or harbor.",
                }
            },
            "required": ["counterparty"],
            "additionalProperties": False,
        },
    },
]


def _write(payload):
    sys.stdout.write(json.dumps(payload, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def _result(msg_id, result):
    _write({"jsonrpc": "2.0", "id": msg_id, "result": result})


def _error(msg_id, code, message):
    _write({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}})


def _handle(msg):
    method = msg.get("method")
    if method and str(method).startswith("notifications/"):
        if method == "notifications/risksphere/reset_book":
            tools.reset()
        return
    msg_id = msg.get("id")
    if method == "initialize":
        _result(msg_id, {
            "protocolVersion": PROTOCOL,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "RiskSphere", "version": "1.0.0"},
            "instructions": "Calculations only. Do not send, approve, or book collateral.",
        })
        return
    if method == "ping":
        _result(msg_id, {})
        return
    if method == "tools/list":
        _result(msg_id, {"tools": TOOLS})
        return
    if method == "tools/call":
        params = msg.get("params") or {}
        name = str(params.get("name") or "")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            _result(msg_id, {
                "content": [{"type": "text", "text": "arguments must be an object"}],
                "isError": True,
            })
            return
        try:
            output = tools.run(name, args)
        except Exception as exc:
            log.error(f"mcp tool {name} failed: {exc}")
            _result(msg_id, {
                "content": [{"type": "text", "text": str(exc)}],
                "isError": True,
            })
            return
        _result(msg_id, {
            "content": [{"type": "text", "text": json.dumps(output)}],
            "structuredContent": output,
            "isError": False,
        })
        return
    _error(msg_id, -32601, f"method not found: {method}")


def main():
    """Read protocol lines until stdin closes. Never prints a log to stdout."""
    log.configure()
    log.trace("mcp server ready")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _error(None, -32700, "parse error")
            continue
        if not isinstance(msg, dict):
            _error(None, -32600, "invalid request")
            continue
        try:
            _handle(msg)
        except Exception as exc:
            log.error(f"mcp handler failed: {exc}")
            if msg.get("id") is not None:
                _error(msg.get("id"), -32603, str(exc))


if __name__ == "__main__":
    main()
