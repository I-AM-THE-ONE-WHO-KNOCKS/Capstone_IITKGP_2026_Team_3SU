"""Planner, guardrails, critic, and the Documents reading call.

This module is the only place that talks to the chat gateway. The orchestrator
asks Qwen for a JSON plan and for the wording. Documents asks Qwen to read one
retrieved clause and propose a term. Python checks that quote before a draft
is written. The gateway sees one request at a time, held by a lock. If the
key is missing, or PFE_LLM_OFF=1, the keyword planner runs and Documents does
not call the model. The Authorization header is never written to the log.
"""

import json
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import books
import log

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
STATE_PATH = ROOT / "data" / "session_state.json"
TRACE_PATH = ROOT / "outputs" / "traces.jsonl"
_LOCK = threading.Lock()
# Counts for a live evaluation. chat() increments these. They are process-local.
CHAT_CALLS = 0
PROMPT_TOKENS = 0
COMPLETION_TOKENS = 0
_CJK = re.compile(r"[\u3400-\u9fff\u3000-\u303f\uff00-\uffef]+")

INJECTION_RE = re.compile(
    r"(ignore (all |any |your |the |previous |prior |above )+.{0,25}(instructions|rules|polic\w+|limits?)"
    r"|disregard (the |all )?(system|previous|prior)|you are now|new instructions\s*:|system\s*:\s*|"
    r"override .{0,30}(limit|rule|policy)|(print|show|reveal|repeat) .{0,25}(system prompt|api key|secret))",
    re.I,
)
ACTION_RE = re.compile(
    r"\b(issue|send|execute|approve|release|book|settle|waive|override|increase|change|raise|pay|transfer)\b.{0,40}"
    r"\b(margin call|the call|limit|trade|collateral|breach)\b"
    r"|\b(margin call|the call|limit|trade|collateral|breach)\b.{0,40}"
    r"\b(issue|send|execute|approve|release|book|settle|waive|pay|transfer)\b",
    re.I,
)
LEGAL_RE = re.compile(r"(legal advice|legally|\bsue\b|terminate the isda)", re.I)
PII_REQ_RE = re.compile(r"\b(ssn|social security|passport|home address|personal phone)\b", re.I)
DOMAIN_RE = re.compile(
    r"(pfe|exposure|margin|collateral|csa|isda|haircut|limit|counterpart|threshold|mta|"
    r"minimum transfer|mtm|credit support|volatil|\bvol\b|price|spot|breach|yahoo|"
    r"eligible|independent amount|scenario|shock|shortfall|surplus|expected loss|"
    r"initial margin|\balerts?\b|\breport\b|disputes?|clause|schedule|stale|data quality|"
    r"data complete|book complete|\bits\b|\bit\b)",
    re.I,
)


def load_config():
    """Read the desk settings fresh, so a prompt edit applies to the next question.

    Returns the JSON object: prompts, token caps, temperature, retry waits,
    log level, and the PFE path count. Raises if the file is missing or
    is not valid JSON.
    """
    with CONFIG_PATH.open() as handle:
        return json.load(handle)


def fill_prompt(template, **values):
    """Substitute {name} placeholders in a prompt template.

    Each keyword argument replaces the matching brace token with its
    string value. Unknown braces are left as written. Returns the filled text.
    """
    text = template
    for key, value in values.items():
        text = text.replace("{" + key + "}", str(value))
    return text


def english_only(text):
    """Drop CJK characters and collapse extra spaces.

    The group gateway is used in English. A model reply that mixes in
    Chinese, Japanese, or Korean is stripped before it is shown or stored.
    None is treated as an empty string. Returns the cleaned text.
    """
    cleaned = _CJK.sub("", text or "")
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip()


def load_secrets():
    """Load KEY=value lines into the process environment.

    Blank lines and comments are skipped. A variable that is already set
    is left alone, so a shell override wins. Missing file is a no-op.
    The values are not logged.
    """
    path = ROOT / "secrets.local.env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def llm_enabled():
    """True when a chat call is allowed.

    False when PFE_LLM_OFF is 1, or when the key or the base URL is missing.
    Callers then use the keyword planner and skip the wording call.
    """
    if os.environ.get("PFE_LLM_OFF") == "1":
        return False
    return bool(os.environ.get("LLM_API_KEY") and os.environ.get("LLM_BASE_URL"))


def load_state():
    """Return the remembered counterparty for this process.

    The only field used today is last_counterparty. A missing or broken
    file returns an empty name, so the first question still runs.
    """
    if not STATE_PATH.exists():
        return {"last_counterparty": ""}
    try:
        return json.loads(STATE_PATH.read_text())
    except json.JSONDecodeError:
        return {"last_counterparty": ""}


def save_state(state):
    """Write the session dict so a later "its" can reuse the last name.

    state is a small dict, typically {"last_counterparty": "<id>"}.
    """
    STATE_PATH.write_text(json.dumps(state))


def trace(record):
    """Append one JSON line for a finished question.

    record should already omit the prompt and the API key. Used for a
    later demo of latency and which tools ran. Creates the outputs folder
    if it is missing.
    """
    TRACE_PATH.parent.mkdir(exist_ok=True)
    with TRACE_PATH.open("a") as handle:
        handle.write(json.dumps(record) + "\n")


def _ssl_context():
    """TLS context for the chat gateway.

    Uses LLM_CA_CERT when it is set, so the group certificate is trusted.
    Otherwise the default trust store is used. Verification stays on.
    """
    cert = os.environ.get("LLM_CA_CERT", "").strip()
    if cert:
        return ssl.create_default_context(cafile=cert)
    return ssl.create_default_context()


def chat(user_text, max_tokens, owner="Orchestrator"):
    """One chat completion. Returns the assistant text with think-tags removed.

    user_text is the full prompt. max_tokens is capped at 1024, which is
    the group limit. Temperature comes from settings. A /no_think line and
    the language rule are prepended when no_think is on. owner is the agent
    that asked, Orchestrator or Documents, and is the name written on the log line.

    Retries HTTP 429 and 503 after the configured waits (2s, 4s, 8s).
    HTTP 401 raises with a key hint. A certificate failure names LLM_CA_CERT.
    The bearer token is sent and never logged. Prompt and reply are logged
    only at debug. A successful reply increments the call and token counters.
    """
    global CHAT_CALLS, PROMPT_TOKENS, COMPLETION_TOKENS
    cfg = load_config()
    if cfg.get("no_think", True):
        user_text = "/no_think\n" + cfg.get("language", "") + "\n" + user_text
    url = os.environ["LLM_BASE_URL"].rstrip("/") + "/chat/completions"
    body = {
        "model": os.environ.get("LLM_MODEL", "Qwen/Qwen3-8B"),
        "messages": [{"role": "user", "content": user_text}],
        "max_tokens": min(int(max_tokens), 1024),
        "temperature": cfg.get("temperature", 0),
    }
    log.work(owner, f"calling {body['model']}, max_tokens={body['max_tokens']}")
    log.debug("llm prompt " + user_text)
    data = json.dumps(body).encode()
    waits = (0,) + tuple(cfg.get("retry_waits_seconds", [2, 4, 8]))
    timeout = cfg.get("request_timeout_seconds", 90)
    last_error = None
    for attempt, wait in enumerate(waits):
        if wait:
            time.sleep(wait)
        request = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + os.environ["LLM_API_KEY"],
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=_ssl_context()) as response:
                payload = json.loads(response.read().decode())
            content = _strip_think(payload["choices"][0]["message"]["content"])
            usage = payload.get("usage") or {}
            CHAT_CALLS += 1
            PROMPT_TOKENS += int(usage.get("prompt_tokens") or 0)
            COMPLETION_TOKENS += int(usage.get("completion_tokens") or 0)
            log.debug("llm response " + content)
            return content
        except urllib.error.HTTPError as exc:
            last_error = exc
            detail = exc.read().decode(errors="replace")[:300]
            if exc.code in (429, 503) and attempt < len(waits) - 1:
                continue
            if exc.code == 401:
                log.error("gateway HTTP 401")
                raise RuntimeError("401 from the gateway. Check the key and the group URL.") from exc
            log.error(f"gateway HTTP {exc.code}: {detail}")
            raise RuntimeError(f"gateway HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            reason = str(exc.reason)
            if "CERTIFICATE" in reason.upper() or "certificate" in reason:
                log.error("certificate check failed")
                raise RuntimeError(
                    "Certificate check failed. Set LLM_CA_CERT to the .crt for this group."
                ) from exc
            log.error(f"could not reach the gateway: {reason}")
            raise RuntimeError(f"could not reach the gateway: {reason}") from exc
    log.error(f"gateway still failing: {last_error}")
    raise RuntimeError(f"gateway still failing: {last_error}")


def _strip_think(text):
    """Remove a <think>...</think> block the model may still emit.

    Returns the remaining text, stripped. The visible answer is what is
    outside that block.
    """
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return text.strip()


def _json_object(text):
    """Parse the first {...} object in a model reply.

    The model sometimes wraps JSON in a sentence. This takes the slice from
    the first brace to the last brace. Raises ValueError when no object is
    present, or json.JSONDecodeError when the slice is not valid JSON.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object in the model reply")
    return json.loads(text[start : end + 1])


def _guard(name, how, passed, ran=True):
    """One guardrail row for the console.

    name is kill, injection, pii, action, legal, or scope.
    how is the sentence that says what was searched.
    passed is True when the question may continue.
    ran is False when an earlier check already stopped the question.
    """
    return {"name": name, "how": how, "passed": passed, "ran": ran}


_GUARD_ORDER = ["kill", "injection", "pii", "action", "legal", "scope"]


def _finish_guards(checks):
    """Pad the check list so every guardrail appears, then name the stop.

    Checks that did not run are appended with ran=False. Returns
    {"stop": <name or None>, "checks": [...]} . stop is the first check
    that ran and failed.
    """
    done = {row["name"] for row in checks}
    for name in _GUARD_ORDER:
        if name not in done:
            checks.append(_guard(
                name,
                "An earlier check already stopped the question, so this one did not look at the text.",
                False,
                ran=False,
            ))
    stop = next((row["name"] for row in checks if row["ran"] and not row["passed"]), None)
    return {"stop": stop, "checks": checks}


def prefilter(question):
    """Run the guardrails in order. The first failure stops the rest.

    The Orchestrator runs these checks. No calculation is called yet.

    Order: kill switch (PFE_KILL_SWITCH=1), prompt injection, personal data,
    an instruction to send or book, legal advice, then scope. Scope passes
    when the text names a counterparty or contains a desk word such as
    exposure, margin, haircut, or price.

    question is the raw analyst text. Returns the dict from _finish_guards.
    Does not call the model and does not read Yahoo.
    """
    log.work("Orchestrator", "checking the question before any calculation runs")
    checks = []
    if os.environ.get("PFE_KILL_SWITCH") == "1":
        checks.append(_guard(
            "kill",
            "Read the environment variable PFE_KILL_SWITCH. It is 1, so RiskSphere refuses before any model call.",
            False,
        ))
        return _finish_guards(checks)
    checks.append(_guard(
        "kill",
        "Read PFE_KILL_SWITCH. It is not set, so this switch is off.",
        True,
    ))

    if INJECTION_RE.search(question):
        checks.append(_guard(
            "injection",
            "Searched the question for an attempt to ignore the rules, or to print the prompt or the key. That pattern matched.",
            False,
        ))
        return _finish_guards(checks)
    checks.append(_guard(
        "injection",
        "Searched the question for an attempt to ignore the rules, or to print the prompt or the key. No match.",
        True,
    ))

    if PII_REQ_RE.search(question):
        checks.append(_guard(
            "pii",
            "Searched for a request for an SSN, a passport, a home address, or a personal phone number. That pattern matched.",
            False,
        ))
        return _finish_guards(checks)
    checks.append(_guard(
        "pii",
        "Searched for a request for an SSN, a passport, a home address, or a personal phone number. No match.",
        True,
    ))

    if ACTION_RE.search(question):
        checks.append(_guard(
            "action",
            "Searched for send, approve, release, book, pay, or change, next to collateral, a limit, a trade, or a call. That pattern matched.",
            False,
        ))
        return _finish_guards(checks)
    checks.append(_guard(
        "action",
        "Searched for send, approve, release, book, pay, or change, next to collateral, a limit, a trade, or a call. No match.",
        True,
    ))

    if LEGAL_RE.search(question):
        checks.append(_guard(
            "legal",
            "Searched for a request for legal advice, or to sue, or to terminate an ISDA. That pattern matched.",
            False,
        ))
        return _finish_guards(checks)
    checks.append(_guard(
        "legal",
        "Searched for a request for legal advice, or to sue, or to terminate an ISDA. No match.",
        True,
    ))

    named = books.find_party(books.names_only(), question)
    if DOMAIN_RE.search(question) or named:
        found = f"Named counterparty: {named}." if named else "A desk word such as exposure, margin, haircut, limit, or price is in the question."
        checks.append(_guard(
            "scope",
            "Checked that the question is about RiskSphere. " + found,
            True,
        ))
        return _finish_guards(checks)
    checks.append(_guard(
        "scope",
        "Looked for a desk word or a name on the book. None of those are in the question, so it is out of scope.",
        False,
    ))
    return _finish_guards(checks)


def _normalize_step(step, cfg):
    """Turn one planned step into a tool name plus clean arguments.

    Exposure, margin, and future-exposure steps keep only a known
    counterparty id. Market data keeps an upper-case ticker. A haircut
    step must use an asset class from settings; a bad maturity or rating
    falls back to 1-5y and AAA-AA-.

    Raises ValueError for an unknown tool, a missing name, or a bad shape.
    Future exposure is a counterparty tool, not a haircut lookup.
    """
    if not isinstance(step, dict):
        raise ValueError("a step must be an object")
    tool = str(step.get("tool", "")).strip()
    allowed = {
        "current_exposure",
        "calculate_margin_call",
        "get_market_data",
        "regulatory_haircut",
        "check_pfe_limit",
        "check_data_quality",
        "scenario_pnl",
        "initial_margin_proxy",
        "collateral_coverage",
        "expected_loss",
        "search_clauses",
        "propose_term",
        "list_alerts",
        "draft_report",
        "draft_margin_notice",
        "draft_stress_book",
        "draft_dispute",
    }
    if tool not in allowed:
        raise ValueError(f"unknown tool {tool}")
    args = step.get("args") or {}
    if not isinstance(args, dict):
        raise ValueError("args must be an object")
    raw_args = args
    party_tools = {
        "current_exposure",
        "calculate_margin_call",
        "check_pfe_limit",
        "scenario_pnl",
        "initial_margin_proxy",
        "collateral_coverage",
        "expected_loss",
        "draft_report",
        "draft_margin_notice",
        "draft_dispute",
        "propose_term",
    }
    if tool in party_tools:
        cid = books.find_party(books.names_only(), str(raw_args.get("counterparty", "")))
        if not cid:
            raise ValueError(f"{tool} needs a counterparty")
        args = {"counterparty": cid}
        if tool == "scenario_pnl":
            scenario = str(raw_args.get("scenario") or "")
            if scenario not in (cfg.get("scenarios") or {}):
                raise ValueError("scenario must be one of " + ", ".join(cfg.get("scenarios") or []))
            args["scenario"] = scenario
        elif tool == "expected_loss":
            basis = str(raw_args.get("ead_basis") or "current")
            if basis not in ("current", "pfe"):
                basis = "current"
            args["ead_basis"] = basis
        elif tool == "draft_report":
            args["include_pfe"] = bool(raw_args.get("include_pfe"))
        elif tool == "draft_dispute":
            try:
                args["their_mtm"] = float(raw_args.get("their_mtm"))
            except (TypeError, ValueError) as exc:
                raise ValueError("draft_dispute needs their_mtm") from exc
        elif tool == "propose_term":
            field = str(raw_args.get("field") or "")
            if field not in ("threshold", "mta", "rounding", "independent_amount"):
                raise ValueError("propose_term field is not one of the agreement numbers")
            try:
                value = float(raw_args.get("value"))
            except (TypeError, ValueError) as exc:
                raise ValueError("propose_term needs a value") from exc
            passage_id = str(raw_args.get("passage_id") or "").strip()
            if not passage_id:
                raise ValueError("propose_term needs a passage_id")
            args["field"] = field
            args["value"] = value
            args["passage_id"] = passage_id
    elif tool == "get_market_data":
        ticker = str(raw_args.get("ticker", "")).strip().upper()
        if not ticker:
            raise ValueError("get_market_data needs a ticker")
        args = {"ticker": ticker}
    elif tool == "regulatory_haircut":
        asset = str(raw_args.get("asset_class", "")).strip()
        if asset not in cfg["asset_classes"]:
            raise ValueError(f"asset_class must be one of {cfg['asset_classes']}")
        maturity = str(raw_args.get("maturity_bucket") or "1-5y")
        rating = str(raw_args.get("rating_bucket") or "AAA-AA-")
        if maturity not in cfg["maturity_buckets"]:
            maturity = "1-5y"
        if rating not in cfg["rating_buckets"]:
            rating = "AAA-AA-"
        args = {"asset_class": asset, "maturity_bucket": maturity, "rating_bucket": rating}
    elif tool == "search_clauses":
        pile = str(raw_args.get("pile") or "csa")
        if pile not in ("csa", "d499"):
            pile = "csa"
        query = str(raw_args.get("query") or "").strip()
        if not query:
            raise ValueError("search_clauses needs a query")
        args = {"query": query[:500], "pile": pile}
    elif tool == "draft_stress_book":
        cid = books.find_party(books.names_only(), str(raw_args.get("counterparty", "")))
        args = {"counterparty": cid}
    elif tool in ("check_data_quality", "list_alerts"):
        args = {}
    else:
        raise ValueError(f"unknown tool {tool}")
    return {"tool": tool, "args": args}


def validate_plan(plan, cfg):
    """Accept a planner object or raise ValueError.

    Requires a steps list of at most max_steps (default 4). Strips non-English
    characters from the reason fields. A refuse flag clears the steps.
    An empty step list on a non-refusal is an error. Each step is passed
    through _normalize_step. Returns the same dict, mutated.
    """
    if not isinstance(plan, dict) or not isinstance(plan.get("steps"), list):
        raise ValueError("plan must be an object with a steps list")
    if len(plan["steps"]) > cfg.get("max_steps", 4):
        raise ValueError("at most 4 steps")
    plan["refuse"] = bool(plan.get("refuse"))
    plan["refusal_reason"] = english_only(str(plan.get("refusal_reason") or ""))
    plan["reason"] = english_only(str(plan.get("reason") or ""))
    plan["intent"] = english_only(str(plan.get("intent") or "")) or "unspecified"
    if plan["refuse"]:
        plan["steps"] = []
        return plan
    if not plan["steps"]:
        raise ValueError("plan has no steps")
    plan["steps"] = [_normalize_step(step, cfg) for step in plan["steps"]]
    return plan


def _fill_last(question, plan, state):
    """Fill a blank counterparty from the question, then from the session.

    Used after the model returns a plan. A step that already names someone
    is left alone. A name in the question wins over the remembered name.
    Returns the plan.
    """
    last = state.get("last_counterparty") or ""
    named = books.find_party(books.names_only(), question)
    for step in plan.get("steps") or []:
        if "counterparty" not in step["args"]:
            continue
        if step["args"]["counterparty"]:
            continue
        step["args"]["counterparty"] = named or last
    return plan


def _stated_amount(question):
    """First large number written in the question, or None.

    Used when a dispute question already states the other side's mark.
    Commas are removed. A number under 1000 is ignored so a year is not
    treated as a mark.
    """
    for match in re.finditer(r"\d{1,3}(?:,\d{3})+|\d{4,}", question or ""):
        number = float(match.group(0).replace(",", ""))
        if number >= 1000:
            return number
    return None


def heuristic_plan(question, state):
    """Choose tools from keywords when the model is off or the gateway failed.

    A haircut question with no counterparty name becomes a table lookup.
    "Over 5 years" maps to the >5y bucket. A named ticker plus price, spot,
    vol, or yahoo adds a market-data step. "PFE" or "future exposure" adds
    the future-exposure check and does not also add today's exposure.
    "Limit" on its own still means today's exposure. The remembered name is
    used only when the question says it, its, their, or them.

    question is the analyst text. state holds last_counterparty.
    Returns a plan dict with router "rules". If nothing matches, refuse is
    true and no tool runs. Does not call the model.
    """
    log.work("Orchestrator", "choosing the steps from keywords, model not called")
    cfg = load_config()
    lowered = question.lower()
    named = books.find_party(books.names_only(), question)
    follow = bool(re.search(r"\b(it|its|their|them)\b", lowered))
    cid = named or (state.get("last_counterparty") if follow else "") or ""
    steps = []
    wants_quality = bool(re.search(r"data quality|schema|missing field|validate the book|book complete|data complete", lowered))
    wants_doc = bool(re.search(r"what does|clause|wording of|agreement say|csa say|schedule say", lowered))
    wants_alerts = bool(re.search(r"\balerts?\b|stale price", lowered))
    wants_report = bool(re.search(r"\breport\b|daily pack", lowered))
    wants_notice = bool(re.search(r"margin notice|draft (a |the )?(notice|call)", lowered))
    wants_stress = bool(re.search(r"\bstress\b", lowered))
    wants_dispute = bool(re.search(r"\bdisputes?\b", lowered))
    wants_scenario = bool(re.search(r"scenario|shock|what if|falls|down 20|down 40", lowered))
    wants_im = bool(re.search(r"initial margin|im proxy|\bim\b", lowered))
    wants_cover = bool(re.search(r"shortfall|surplus|coverage|eligible collateral", lowered))
    wants_el = bool(re.search(r"expected loss|\bpd\b|\blgd\b|probability of default", lowered))
    wants_pfe = bool(re.search(r"\bpfe\b|future exposure|potential future", lowered))
    wants_margin = bool(re.search(
        r"\bmargin\b|\bcall\b|threshold|mta|delivery|collateral requirement", lowered
    )) and not (wants_im or wants_notice or wants_cover or wants_doc)
    wants_exposure = bool(re.search(r"exposure|limit|breach|headroom|\bmark\b", lowered)) and not (
        wants_pfe or wants_el or wants_dispute
    )
    if wants_quality:
        steps.append({"tool": "check_data_quality", "args": {}})
    if wants_doc:
        pile = "d499" if re.search(r"d499|schedule|haircut", lowered) else "csa"
        steps.append({"tool": "search_clauses", "args": {"query": question, "pile": pile}})
    if "haircut" in lowered and not named and not wants_doc:
        asset = "cash"
        for name in ("gold", "equity", "corporate", "government"):
            if name in lowered:
                asset = "equity_main_index" if name == "equity" else name
                break
        maturity = ">5y" if re.search(r"over 5|more than 5|>\s*5", lowered) else "1-5y"
        if re.search(r"under 1|less than 1|<=\s*1", lowered):
            maturity = "<=1y"
        rating = "A+-BBB-" if re.search(r"a\+|bbb", lowered) else "AAA-AA-"
        steps.append({
            "tool": "regulatory_haircut",
            "args": {"asset_class": asset, "maturity_bucket": maturity, "rating_bucket": rating},
        })
    ticker = re.search(r"\b(SPY|SHY|LQD|EURUSD=X|QQQ|GLD|TLT)\b", question, re.I)
    if ticker and re.search(r"price|spot|vol|yahoo", lowered) and not wants_scenario:
        steps.append({"tool": "get_market_data", "args": {"ticker": ticker.group(1).upper()}})
    if cid and wants_scenario:
        if re.search(r"\b40\b", lowered):
            scenario = "equity_down_40"
        elif re.search(r"usd|fx|eur", lowered):
            scenario = "usd_spike"
        else:
            scenario = "equity_down_20"
        steps.append({"tool": "scenario_pnl", "args": {"counterparty": cid, "scenario": scenario}})
    if cid and wants_im:
        steps.append({"tool": "initial_margin_proxy", "args": {"counterparty": cid}})
    if cid and wants_cover:
        steps.append({"tool": "collateral_coverage", "args": {"counterparty": cid}})
    if cid and wants_el:
        basis = "pfe" if wants_pfe else "current"
        steps.append({"tool": "expected_loss", "args": {"counterparty": cid, "ead_basis": basis}})
    if cid and wants_pfe and not wants_el:
        steps.append({"tool": "check_pfe_limit", "args": {"counterparty": cid}})
    if wants_alerts:
        steps.append({"tool": "list_alerts", "args": {}})
    if cid and wants_report:
        steps.append({
            "tool": "draft_report",
            "args": {"counterparty": cid, "include_pfe": wants_pfe},
        })
    if cid and wants_notice:
        steps.append({"tool": "draft_margin_notice", "args": {"counterparty": cid}})
    if wants_stress:
        steps.append({"tool": "draft_stress_book", "args": {"counterparty": cid}})
    if cid and wants_dispute:
        amount = _stated_amount(question)
        if amount is not None:
            steps.append({
                "tool": "draft_dispute",
                "args": {"counterparty": cid, "their_mtm": amount},
            })
    if cid and wants_margin and not any(step["tool"] == "calculate_margin_call" for step in steps):
        steps.append({"tool": "calculate_margin_call", "args": {"counterparty": cid}})
    if cid and wants_exposure and not any(step["tool"] == "current_exposure" for step in steps):
        steps.append({"tool": "current_exposure", "args": {"counterparty": cid}})
    if cid and not steps:
        steps.append({"tool": "current_exposure", "args": {"counterparty": cid}})
    steps = steps[: cfg.get("max_steps", 4)]
    plan = {
        "intent": "heuristic",
        "refuse": False,
        "refusal_reason": "",
        "reason": "keyword plan, model was not called",
        "steps": steps,
        "router": "rules",
    }
    if not steps:
        plan["refuse"] = True
        plan["refusal_reason"] = "no tool matched"
    return validate_plan(plan, cfg) if steps else plan


def term_request(question):
    """True when the question asks for an agreement number, not a schedule sentence.

    Threshold, minimum transfer, rounding, and independent amount are the
    fields Documents may propose. A haircut-schedule question stays a retrieval
    and does not call Qwen a second time.
    """
    return bool(re.search(
        r"threshold|minimum transfer|\bmta\b|\brounding\b|independent amount",
        question or "",
        re.I,
    ))


def read_clause(question, hits, counterparty):
    """Ask Qwen, as Documents, to propose one term from retrieved passages.

    hits are the search_clauses rows. counterparty is a name or id, or empty.
    The model must return a field, a passage id, one amount, and a quote.
    Python runs the quote gate. A draft is written only when that gate says
    VERIFIED, and the stored agreement is not edited. Returns the reading.
    Raises if the gateway fails or the reply has no JSON object.
    """
    import stage4
    log.work("Documents", "asking Qwen to read one clause and propose a term")
    cfg = load_config()
    blocks = []
    for hit in (hits or [])[:3]:
        text = (hit.get("text") or "")[:700]
        blocks.append(f"[{hit.get('source') or 'S?'}] id={hit.get('id')}\n{text}")
    prompt = fill_prompt(
        cfg["read_prompt"],
        question=question,
        passages="\n\n".join(blocks) or "(no passage was retrieved)",
    )
    with _LOCK:
        raw = chat(prompt, max_tokens=cfg.get("read_max_tokens", 400), owner="Documents")
    judged = stage4.judge_reading(_json_object(raw), hits)
    judged["model"] = os.environ.get("LLM_MODEL", "Qwen/Qwen3-8B")
    cid = books.find_party(books.names_only(), str(counterparty or "")) if counterparty else ""
    if judged["status"] == "VERIFIED" and cid:
        try:
            draft = stage4.propose_term(cid, judged["field"], judged["amount"], judged["passage_id"])
        except ValueError as exc:
            judged["status"] = "REVIEW"
            judged["reason"] = str(exc)
            log.work("Documents", f"quote checked, draft not written: {exc}")
        else:
            judged["draft_status"] = draft["status"]
            judged["proposed_value"] = draft["proposed_value"]
            judged["current_value"] = draft["current_value"]
            judged["counterparty_id"] = draft["counterparty_id"]
            judged["applied"] = False
            log.work("Documents", "quote verified, draft written, book unchanged")
    elif judged["status"] == "VERIFIED":
        judged["status"] = "REVIEW"
        judged["reason"] = "quote checked; name a counterparty before a draft is written"
        log.work("Documents", "quote checked, no counterparty, draft not written")
    else:
        log.work("Documents", f"quote {judged['status']}, draft not written")
    return judged


def plan_with_model(question, state):
    """Ask Qwen for one JSON plan and validate it.

    The prompt lists the book names, the last counterparty, and the allowed
    haircut buckets. The call is serialized with the wording call so the
    gateway never sees two active requests. router is set to "qwen3-8b".
    The filled prompt is kept on the plan for the debug log and is stripped
    before the trace line is written.

    Raises if the reply has no JSON object or the plan fails validate_plan.
    """
    log.work("Orchestrator", "asking Qwen for one plan")
    cfg = load_config()
    names = ", ".join(
        f"{row['name']} = {row['counterparty_id']}" for row in books.names_only()
    )
    prompt = fill_prompt(
        cfg["plan_prompt"],
        names=names,
        question=question,
        last_counterparty=state.get("last_counterparty") or "none",
        asset_classes=", ".join(cfg["asset_classes"]),
        maturity_buckets=", ".join(cfg["maturity_buckets"]),
        rating_buckets=", ".join(cfg["rating_buckets"]),
        scenario_names=", ".join((cfg.get("scenarios") or {}).keys()),
    )
    with _LOCK:
        raw = chat(prompt, max_tokens=cfg["plan_max_tokens"])
    plan = validate_plan(_json_object(raw), cfg)
    plan = _fill_last(question, plan, state)
    plan["router"] = "qwen3-8b"
    plan["prompt"] = prompt
    return plan


def compose(question, tool_result, issues=None):
    """Ask Qwen to phrase a result that Python already computed.

    tool_result is the list of successful tool rows. issues, when set, is
    the critic's complaint from the previous draft, appended so the rewrite
    does not add new figures. Returns (sentences, prompt). The sentences
    have CJK characters removed. Does not change any number.
    """
    log.work("Orchestrator", "writing the sentences from the tool result")
    cfg = load_config()
    prompt = fill_prompt(
        cfg["compose_prompt"],
        question=question,
        tool_result=json.dumps(tool_result),
    )
    if issues:
        prompt += "\nThe previous draft failed these checks: " + "; ".join(issues) + ". Rewrite it. Do not add new figures."
    with _LOCK:
        text = chat(prompt, max_tokens=cfg["compose_max_tokens"])
    return english_only(text), prompt


def critic(text, tool_result):
    """Reject wording that invents a figure or talks about a tool that did not run.

    Large numbers in the sentences must appear as digits in the tool JSON.
    A BREACH status cannot be described as within the limit, and an OK
    status cannot be called a breach. Future exposure must quote the peak
    and the status. Today's exposure must quote the exposure and the status.
    A margin amount must be quoted when one exists, and a zero call must
    say there is no call. A haircut percent and a spot must show up too.

    If the wording mentions a margin call, or says collateral must be
    reviewed, the margin tool has to be in tool_result. That stops a
    future-exposure answer from inventing a call.

    text is the draft. tool_result is the list of tool rows.
    Returns a list of issue strings. An empty list means the draft may stay.
    """
    log.work("Orchestrator", "checking the sentences against the tool result")
    issues = []
    blob = json.dumps(tool_result)
    allowed = set(re.findall(r"\d{3,}", blob))
    spoken = set(re.findall(r"\d{1,3}(?:,\d{3})+|\d{4,}", text))
    bad = sorted(item.replace(",", "") for item in spoken if item.replace(",", "") not in allowed)
    if bad:
        issues.append("ungrounded numbers: " + ", ".join(bad))
    low = text.lower()
    for row in tool_result:
        output = row.get("output") or {}
        status = str(output.get("status") or "")
        if status == "BREACH" and re.search(r"within (its |the )?limit|no breach|not in breach", low):
            issues.append("wording says within the limit but status is BREACH")
        if status == "OK" and re.search(r"\bbreach", low) and "no breach" not in low:
            issues.append("wording mentions a breach but status is OK")
        if "BREACH" in text.upper() and status != "BREACH" and "BREACH" not in blob:
            issues.append("status BREACH is not in the tool result")
        plain = text.replace(",", "")
        if row.get("tool") == "check_pfe_limit":
            fig = str(int(output.get("peak_pfe_collateralized_usd") or 0))
            if fig not in plain:
                issues.append("PFE figure missing from the wording")
            if output.get("status") and output["status"] not in text:
                issues.append("PFE status missing from the wording")
        if row.get("tool") == "current_exposure":
            fig = str(int(output.get("current_exposure_usd") or 0))
            if fig not in plain:
                issues.append("current exposure missing from the wording")
            if output.get("status") and output["status"] not in text:
                issues.append("status missing from the wording")
        if row.get("tool") == "calculate_margin_call":
            amount = output.get("delivery_amount_usd") or output.get("return_amount_usd") or 0
            if amount and str(int(amount)) not in plain:
                issues.append("margin amount missing from the wording")
            if not amount and not any(phrase in low for phrase in ("no margin", "no call", "no action")):
                issues.append("wording does not say there is no call")
        if row.get("tool") == "regulatory_haircut":
            pct = output.get("haircut_pct")
            shown = str(pct).rstrip("0").rstrip(".") if pct is not None else ""
            if shown and shown not in plain:
                issues.append("haircut percent missing from the wording")
        if row.get("tool") == "get_market_data" and str(output.get("spot")) not in text:
            issues.append("spot missing from the wording")
        plain_abs = text.replace(",", "")

        def has_fig(fig):
            if fig is None:
                return True
            return str(abs(int(round(float(fig))))) in plain_abs

        if row.get("tool") == "scenario_pnl" and output.get("scenario_pnl_usd") and not has_fig(output["scenario_pnl_usd"]):
            issues.append("scenario pnl missing from the wording")
        if row.get("tool") == "initial_margin_proxy" and not has_fig(output.get("initial_margin_usd")):
            issues.append("initial margin missing from the wording")
        if row.get("tool") == "collateral_coverage":
            fig = output.get("shortfall_usd") or output.get("surplus_usd") or 0
            if fig and not has_fig(fig):
                issues.append("coverage figure missing from the wording")
        if row.get("tool") == "expected_loss" and not has_fig(output.get("expected_loss_usd")):
            issues.append("expected loss missing from the wording")
        if row.get("tool") == "search_clauses":
            hits = output.get("hits") or []
            passages = [hit.get("text") or "" for hit in hits]
            sources = {hit.get("source") for hit in hits if hit.get("source")}
            for quoted in re.findall(r'"([^"]{8,})"', text or ""):
                if not any(quoted in passage for passage in passages):
                    issues.append("quote is not in the passage")
            for cited in re.findall(r"\[(S\d+)\]", text or ""):
                if cited not in sources:
                    issues.append("wording cites a source that was not returned")
        if row.get("tool") == "read_term":
            if re.search(r"stored agreement (was|has been) (updated|changed|edited)", low):
                issues.append("wording says the agreement changed but the draft was not applied")
            if output.get("status") == "VERIFIED" and output.get("amount") is not None:
                fig = str(int(output["amount"]))
                if fig not in plain and f"{int(output['amount']):,}" not in text:
                    issues.append("draft amount missing from the wording")
        if str(row.get("tool") or "").startswith("draft_") and "draft" not in low:
            issues.append("wording does not say the file is a draft")
        if row.get("tool") == "check_data_quality" and not output.get("passed") and not output.get("problems"):
            issues.append("data check failed but the wording has no problem")
    if re.search(r"\bsimm\b", low) and not re.search(r"not\b.{0,24}\bsimm\b", low):
        issues.append("wording calls a figure SIMM")
    if re.search(r"regulatory ead|sa-ccr|saccr", low) and not re.search(r"not\b.{0,40}(regulatory ead|sa-ccr|saccr)", low):
        issues.append("wording presents the figure as regulatory")
    ran = {row.get("tool") for row in tool_result}
    if "calculate_margin_call" not in ran and re.search(r"margin call|no margin|no call", low):
        issues.append("wording talks about a margin call but that tool did not run")
    if "calculate_margin_call" not in ran and re.search(r"before any collateral", low):
        issues.append("wording says collateral must be reviewed but no margin amount was calculated")
    return issues
