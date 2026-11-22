"""Build the trace dashboard from the questions that actually ran.

Reads the append-only trace log and writes one HTML file. Each row is one
finished question: the text, the plan, the tools, the flags, and the
latency. The prompt and the API key are not in the log, and this page does
not add them.

Open the HTML file in a browser. Nothing is served, and nothing is sent.
"""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
TRACE_PATH = ROOT / "outputs" / "traces.jsonl"
OUT_PATH = ROOT / "deliverables" / "trace_dashboard.html"
TZ = ZoneInfo("Asia/Kolkata")


def _load():
    """Return the trace rows that parse. A blank or broken line is skipped."""
    if not TRACE_PATH.exists():
        return []
    rows = []
    for line in TRACE_PATH.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _percentile(values, fraction):
    """Linear-interpolated percentile. An empty list returns 0."""
    if not values:
        return 0.0
    ordered = sorted(values)
    pos = fraction * (len(ordered) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    weight = pos - lo
    return ordered[lo] * (1 - weight) + ordered[hi] * weight


def _stamp(ts):
    """Local time for a unix timestamp, or a dash when the field is missing."""
    if not isinstance(ts, (int, float)):
        return ""
    return datetime.fromtimestamp(ts, TZ).strftime("%Y-%m-%d %H:%M:%S")


def _view(row):
    """One execution, flattened for the page. No prompt, no key."""
    plan = row.get("plan") or {}
    steps = plan.get("steps") or []
    tools = []
    for step in steps:
        if isinstance(step, dict) and step.get("tool"):
            tools.append(step["tool"])
    ran = []
    ok = True
    for tool in row.get("tools") or []:
        name = tool.get("tool") or ""
        if name:
            ran.append(name)
        if tool.get("ok") is False:
            ok = False
    if not (row.get("tools") or []):
        ok = not bool(plan.get("refuse") or row.get("flags"))
    return {
        "time": _stamp(row.get("ts")),
        "query": row.get("query") or "",
        "router": plan.get("router") or "",
        "intent": plan.get("intent") or "",
        "refused": bool(plan.get("refuse")) or any(
            str(flag).startswith("refused:") for flag in (row.get("flags") or [])
        ),
        "tools": ran or tools,
        "flags": list(row.get("flags") or []),
        "latency_s": row.get("latency_s"),
        "ok": ok and not any(str(flag) == "tool_error" for flag in (row.get("flags") or [])),
    }


def _counts(rows, key):
    """Frequency of a string field, highest first."""
    tally = {}
    for row in rows:
        value = row.get(key) or "(none)"
        tally[value] = tally.get(value, 0) + 1
    return sorted(tally.items(), key=lambda item: (-item[1], item[0]))


def _html(payload):
    """One self-contained page. The executions are a JSON blob in the file."""
    data = json.dumps(payload, ensure_ascii=True).replace("<", "\\u003c")
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RiskSphere trace dashboard</title>
<style>
  :root {
    --ink: #1c1915;
    --muted: #5c564c;
    --line: #e4ddd2;
    --paper: #f7f4ee;
    --card: #fffdf9;
    --accent: #8c3a2f;
    --ok: #1f6b45;
    --bad: #8c3a2f;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--paper);
    color: var(--ink);
    font: 15px/1.45 "Iowan Old Style", Palatino, Georgia, serif;
  }
  header, main { max-width: 1100px; margin: 0 auto; padding: 28px 20px; }
  header { padding-bottom: 0; }
  h1 { font-size: 28px; font-weight: 600; margin: 0 0 6px; letter-spacing: -0.02em; }
  h2 { font-size: 18px; font-weight: 600; margin: 28px 0 10px; }
  p.lead { color: var(--muted); margin: 0; max-width: 68ch; }
  .cards { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-top: 22px; }
  .card { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; }
  .card .k { color: var(--muted); font-size: 12px; letter-spacing: 0.04em; text-transform: uppercase; }
  .card .v { font-size: 26px; margin-top: 4px; font-variant-numeric: tabular-nums; }
  .card .s { color: var(--muted); font-size: 13px; margin-top: 2px; }
  .split { display: grid; grid-template-columns: 1.2fr 0.8fr; gap: 18px; }
  table { width: 100%; border-collapse: collapse; background: var(--card); }
  th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
  th { font-size: 12px; letter-spacing: 0.04em; text-transform: uppercase; color: var(--muted); font-weight: 600; }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
  .bar { height: 8px; background: #efe8de; border-radius: 99px; overflow: hidden; min-width: 80px; }
  .bar > span { display: block; height: 100%; background: var(--accent); }
  .controls { display: flex; gap: 10px; flex-wrap: wrap; margin: 8px 0 12px; }
  input, select {
    font: inherit; color: var(--ink); background: var(--card);
    border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px;
  }
  input[type="search"] { min-width: 280px; flex: 1; }
  .tag {
    display: inline-block; margin: 0 4px 4px 0; padding: 1px 7px; border-radius: 99px;
    background: #efe8de; font-size: 12px;
  }
  .tag.flag { background: #f3e2dc; color: var(--bad); }
  .ok { color: var(--ok); }
  .bad { color: var(--bad); }
  .empty { color: var(--muted); padding: 18px 10px; }
  footer { color: var(--muted); font-size: 13px; margin-top: 18px; }
  @media (max-width: 800px) {
    .cards, .split { grid-template-columns: 1fr; }
  }
</style>
</head>
<body>
<header>
  <h1>RiskSphere trace dashboard</h1>
  <p class="lead">Each row is one question the console or the evaluation harness finished. The plan, the tools, the flags, and the latency are what was recorded. The prompt and the API key are not in this log.</p>
  <div class="cards" id="cards"></div>
</header>
<main>
  <div class="split">
    <section>
      <h2>Tools that ran</h2>
      <table id="tools"></table>
    </section>
    <section>
      <h2>Flags</h2>
      <table id="flags"></table>
    </section>
  </div>
  <h2>Executions</h2>
  <div class="controls">
    <input id="q" type="search" placeholder="Filter by question text" aria-label="Filter by question text">
    <select id="router" aria-label="Router"></select>
    <select id="flag" aria-label="Flag"></select>
  </div>
  <table>
    <thead>
      <tr>
        <th>Time</th>
        <th>Question</th>
        <th>Router</th>
        <th>Tools</th>
        <th>Flags</th>
        <th class="num">Latency</th>
      </tr>
    </thead>
    <tbody id="rows"></tbody>
  </table>
  <p class="empty" id="none" hidden>No execution matches that filter.</p>
  <footer id="foot"></footer>
</main>
<script>
const DATA = __DATA__;

function card(label, value, sub) {
  return `<div class="card"><div class="k">${label}</div><div class="v">${value}</div><div class="s">${sub}</div></div>`;
}

function bars(target, pairs, total) {
  const width = Math.max(...pairs.map((p) => p[1]), 1);
  target.innerHTML = `<thead><tr><th>Name</th><th class="num">Count</th><th></th></tr></thead><tbody>` +
    pairs.map(([name, count]) => `<tr><td>${esc(name)}</td><td class="num">${count}</td><td><div class="bar"><span style="width:${(100 * count / width).toFixed(1)}%"></span></div></td></tr>`).join("") +
    `</tbody>`;
}

function esc(text) {
  return String(text).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}

function tags(items, cls) {
  if (!items.length) return '<span class="tag">none</span>';
  return items.map((item) => `<span class="tag ${cls || ""}">${esc(item)}</span>`).join("");
}

const cards = document.getElementById("cards");
const s = DATA.summary;
cards.innerHTML = [
  card("Executions", s.executions, s.generated),
  card("Plans from Qwen", s.qwen, s.rules + " other plans, " + s.no_plan + " with no plan"),
  card("Latency p50 / p95", s.p50 + "s / " + s.p95 + "s", "Qwen plans " + s.qwen_p50 + "s / " + s.qwen_p95 + "s"),
  card("Refusals", s.refusals, s.tool_errors + " tool errors"),
].join("");

bars(document.getElementById("tools"), DATA.tools, s.executions);
bars(document.getElementById("flags"), DATA.flags, s.executions);

const router = document.getElementById("router");
const flag = document.getElementById("flag");
router.innerHTML = `<option value="">All routers</option>` + DATA.routers.map(([name]) => `<option>${esc(name)}</option>`).join("");
flag.innerHTML = `<option value="">All flags</option>` + DATA.flags.map(([name]) => `<option>${esc(name)}</option>`).join("");

const body = document.getElementById("rows");
const none = document.getElementById("none");

function draw() {
  const text = document.getElementById("q").value.trim().toLowerCase();
  const wantRouter = router.value;
  const wantFlag = flag.value;
  const shown = DATA.rows.filter((row) => {
    if (text && !row.query.toLowerCase().includes(text)) return false;
    if (wantRouter && (row.router || "(none)") !== wantRouter) return false;
    if (wantFlag && !row.flags.includes(wantFlag)) return false;
    return true;
  });
  body.innerHTML = shown.map((row) => `<tr>
    <td>${esc(row.time)}</td>
    <td>${esc(row.query)}</td>
    <td>${esc(row.router || "(none)")}</td>
    <td>${tags(row.tools)}</td>
    <td>${tags(row.flags, "flag")}</td>
    <td class="num">${row.latency_s == null ? "" : Number(row.latency_s).toFixed(3)}</td>
  </tr>`).join("");
  none.hidden = shown.length > 0;
  document.getElementById("foot").textContent = shown.length + " of " + DATA.rows.length + " executions shown. Times are Asia/Kolkata.";
}

document.getElementById("q").addEventListener("input", draw);
router.addEventListener("change", draw);
flag.addEventListener("change", draw);
draw();
</script>
</body>
</html>
""".replace("__DATA__", data)


def main():
    """Write the dashboard next to the reports."""
    raw = _load()
    rows = [_view(row) for row in raw]
    rows.sort(key=lambda row: row["time"], reverse=True)
    latencies = [row["latency_s"] for row in rows if isinstance(row["latency_s"], (int, float))]
    qwen = [row["latency_s"] for row in rows if row["router"] == "qwen3-8b" and isinstance(row["latency_s"], (int, float))]
    tool_tally = {}
    flag_tally = {}
    for row in rows:
        for tool in row["tools"]:
            tool_tally[tool] = tool_tally.get(tool, 0) + 1
        for flag in row["flags"]:
            flag_tally[flag] = flag_tally.get(flag, 0) + 1
    routers = _counts(rows, "router")
    payload = {
        "summary": {
            "executions": len(rows),
            "qwen": sum(1 for row in rows if row["router"] == "qwen3-8b"),
            "rules": sum(1 for row in rows if row["router"] and row["router"] != "qwen3-8b"),
            "no_plan": sum(1 for row in rows if not row["router"]),
            "refusals": sum(1 for row in rows if row["refused"]),
            "tool_errors": flag_tally.get("tool_error", 0),
            "p50": f"{_percentile(latencies, 0.50):.3f}",
            "p95": f"{_percentile(latencies, 0.95):.3f}",
            "qwen_p50": f"{_percentile(qwen, 0.50):.3f}",
            "qwen_p95": f"{_percentile(qwen, 0.95):.3f}",
            "generated": datetime.now(TZ).strftime("%Y-%m-%d %H:%M"),
        },
        "tools": sorted(tool_tally.items(), key=lambda item: (-item[1], item[0])),
        "flags": sorted(flag_tally.items(), key=lambda item: (-item[1], item[0])),
        "routers": routers,
        "rows": rows,
    }
    OUT_PATH.parent.mkdir(exist_ok=True)
    OUT_PATH.write_text(_html(payload))
    print(f"wrote {OUT_PATH}")
    print(f"executions {len(rows)}")


if __name__ == "__main__":
    main()
