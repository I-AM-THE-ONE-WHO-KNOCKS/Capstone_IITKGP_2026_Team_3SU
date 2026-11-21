"""Shared fixtures. Every test is isolated from the developer's real files.

- session state and the trace log go to a temp dir, never data/ or outputs/
- the LLM and embedding gateways are switched off, so offline runs make no
  network call even when secrets.local.env exists
- the MCP subprocess is always closed
"""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import agent  # noqa: E402
import mcp_client  # noqa: E402

LIVE = os.environ.get("RUN_LIVE_EVAL") == "1"

MOCK_QUOTES = {
    "SPY": {"symbol": "SPY", "price": 754.37, "asof": "2026-11-13", "currency": "USD", "csv": "data/market/SPY.csv"},
    "EURUSD=X": {"symbol": "EURUSD=X", "price": 1.08, "asof": "2026-11-13", "currency": "USD", "csv": "data/market/EURUSD_X.csv"},
    "SHY": {"symbol": "SHY", "price": 82.0, "asof": "2026-11-13", "currency": "USD", "csv": "data/market/SHY.csv"},
    "LQD": {"symbol": "LQD", "price": 108.0, "asof": "2026-11-13", "currency": "USD", "csv": "data/market/LQD.csv"},
}


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Temp state/trace files, gateways off, kill switch off."""
    monkeypatch.setattr(agent, "STATE_PATH", tmp_path / "session_state.json")
    monkeypatch.setattr(agent, "TRACE_PATH", tmp_path / "traces.jsonl")
    # A locally fetched document index must not change test results on one machine only.
    monkeypatch.setattr("books.DOC_INDEX_DIR", tmp_path / "no_doc_index")
    if not LIVE:
        # Empty (not unset) so agent.load_secrets() will not fill them in.
        for key in ("LLM_API_KEY", "LLM_BASE_URL", "EMBED_API_KEY", "EMBED_BASE_URL"):
            monkeypatch.setenv(key, "")
        monkeypatch.setenv("PFE_LLM_OFF", "1")
    monkeypatch.setenv("PFE_KILL_SWITCH", "0")
    monkeypatch.delenv("RISKSPHERE_TRADES", raising=False)  # tests always use the sample book
    yield


@pytest.fixture
def cfg():
    return agent.load_config()


@pytest.fixture
def quotes():
    """Recorded Yahoo quotes, so no test depends on data/market/ or the network."""
    return {k: dict(v) for k, v in MOCK_QUOTES.items()}


@pytest.fixture
def mock_market(quotes, monkeypatch):
    monkeypatch.setattr("market.fetch_quotes", lambda symbols=None: quotes)
    return quotes


@pytest.fixture
def mcp():
    """Use the real MCP server; always closed afterwards."""
    yield mcp_client
    mcp_client.stop()
