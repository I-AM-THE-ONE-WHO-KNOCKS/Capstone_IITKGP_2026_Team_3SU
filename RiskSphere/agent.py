"""Planner, guardrails, and critic for RiskSphere.

This module is the only place that talks to the chat gateway. It decides
whether a question is allowed, asks Qwen for a JSON plan, and later checks
that a wording sentence did not invent a number. The gateway sees one
request at a time, held by a lock. If the key is missing, or PFE_LLM_OFF=1,
the keyword planner runs and the caller says the model was not called.
The Authorization header is never written to the log.
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
_CJK = re.compile(r"[\u3400-\u9fff\u3000-\u303f\uff00-\uffef]+")

INJECTION_RE = re.compile(
    r"(ignore (all |any |your |the |previous |prior |above )+.{0,25}(instructions|rules|polic\w+|limits?)"
    r"|disregard (the |all |your |any )?(system|previous|prior|instructions|rules|guardrails?|checks?)"
    r"|forget (everything|all|your|the|previous|prior|what)\b.{0,30}(told|instructions|rules|said|above|before)?"
    r"|(bypass|circumvent|get around|skip|disable|turn off|switch off) (all |any |the |your )*(guardrails?|safeguards?|checks?|filters?|rules|safety|restrictions)"
    r"|(throw away|drop|discard|set aside) (all |any |the |your |my )*(earlier |previous |prior )*(instructions|rules|guardrails?)"
    r"|pretend .{0,30}(checks?|rules|guardrails?|filters?) (are|were|do not|don't|is)"
    r"|(act|behave|respond) as (an? )?(unrestricted|unfiltered|jailbroken)|without (any )?(restrictions|guardrails?|rules)"
    r"|you are now|new instructions\s*:|system\s*:\s*|"
    r"override .{0,30}(limit|rule|policy)|(print|show|reveal|repeat) .{0,25}(system prompt|api key|secret))",
    re.I,
)
_ACTION_VERBS = (
    r"issue|send|execute|approve|authori[sz]e|release|book|settle|waive|override|increase|change|raise|lift|"
    r"pay|transfer|wire|initiate|submit|sign off on"
)
ACTION_RE = re.compile(
    r"\b(" + _ACTION_VERBS + r")\b.{0,40}"
    r"\b(margin( call| transfer)?|the call|limit|trade|collateral|breach|delivery( amount)?|return( amount)?|"
    r"payment|transfer|funds)\b"
    r"|\b(send|issue|release|wire|execute|approve|authori[sz]e)\s+(it|them|this|that)\b"
    # Editing a stored term. A term changes only after a named person approves it, never from a question.
    r"|\b(change|update|edit|rewrite|amend|modify|replace|overwrite|revise|raise|lower|reduce|increase|decrease|set)\b.{0,40}"
    r"\b(thresholds?|mta|minimum transfer( amount)?|rounding|independent amount|clauses?|paragraphs?|csa|agreement|terms?)\b"
    r".{0,40}\b(to|so)\b",
    re.I,
)
LEGAL_RE = re.compile(
    r"(legal (advice|opinion|counsel)|legally|\bsue\b|lawsuit|litigat\w+|\blawyer\b|\battorney\b|\bcourt\b|"
    r"enforceab\w+|terminate the (isda|csa|agreement|contract)|terminat\w+ .{0,25}(isda|csa|agreement))",
    re.I,
)
PII_REQ_RE = re.compile(r"\b(ssn|social security|passport|home address|personal phone|personal mobile)\b", re.I)
DOMAIN_RE = re.compile(
    r"(pfe|exposure|margin|collateral|csa|isda|haircut|limit|counterpart|threshold|mta|"
    r"minimum transfer|mtm|credit support|volatil|\bvol\b|price|spot|breach|yahoo|quote|trading|ticker|"
    r"\bbonds?\b|equit(y|ies)|\bgold\b|discount|"
    r"eligible|independent amount|\bits\b|\bit\b|"
    r"clauses?|paragraph|agreement|annex|\bcfr\b|basel|bcbs|d499|schedule|table b|(publicly traded|government|eligible|gse) debt)",
    re.I,
)
# A desk word does not make a question about something else in scope.
OFFTOPIC_RE = re.compile(
    r"\b(joke|poem|song|lyrics|recipe|weather|cricket|football|movie|horoscope|"
    r"write (me )?an? (python|java|story|essay|script|poem))\b",
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
    """Return the remembered counterparty and any pending tool for this process.

    A missing or broken file returns default empty values.
    """
    default = {"last_counterparty": "", "pending_tool": None}
    if not STATE_PATH.exists():
        return default
    try:
        data = json.loads(STATE_PATH.read_text())
        if isinstance(data, dict):
            default.update(data)
        return default
    except json.JSONDecodeError:
        return default


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


def chat(user_text, max_tokens):
    """One chat completion. Returns the assistant text with think-tags removed.

    user_text is the full prompt. max_tokens is capped at 1024, which is
    the group limit. Temperature comes from settings. A /no_think line and
    the language rule are prepended when no_think is on.

    Retries HTTP 429 and 503 after the configured waits (2s, 4s, 8s).
    HTTP 401 raises with a key hint. A certificate failure names LLM_CA_CERT.
    The bearer token is sent and never logged. Prompt and reply are logged
    only at debug.
    """
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
    log.trace(f"llm call model={body['model']} max_tokens={body['max_tokens']}")
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


def prefilter(question, state=None):
    """Run the guardrails in order. The first failure stops the rest.

    Order: kill switch (PFE_KILL_SWITCH=1), prompt injection, personal data,
    an instruction to send or book, legal advice, then scope. Scope passes
    when the text names a counterparty, contains a desk word, answers a
    pending clarification, or semantically matches a tool exemplar.

    question is the raw analyst text. Returns the dict from _finish_guards.
    Does not call the chat model and does not read Yahoo.
    """
    checks = []
    if os.environ.get("PFE_KILL_SWITCH") == "1":
        checks.append(_guard(
            "kill",
            "Read the environment variable PFE_KILL_SWITCH. It is 1, so the copilot refuses before any model call.",
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
    if OFFTOPIC_RE.search(question) and not named:
        checks.append(_guard(
            "scope",
            "The question asks for something that is not about the book (a joke, a poem, the weather, code). A desk word in it does not change that.",
            False,
        ))
        return _finish_guards(checks)
    if DOMAIN_RE.search(question) or named or _TICKER_RE.search(question):
        found = f"Named counterparty: {named}." if named else "A desk word such as exposure, margin, haircut, limit, or price is in the question."
        checks.append(_guard(
            "scope",
            "Checked that the question is about RiskSphere. " + found,
            True,
        ))
        return _finish_guards(checks)
    if state and state.get("pending_tool"):
        checks.append(_guard(
            "scope",
            "Answering a pending clarification for a RiskSphere tool.",
            True,
        ))
        return _finish_guards(checks)
    import search
    sem_hits = search.match_intents(question, threshold=float(load_config().get("scope_threshold", 0.62)))
    # "What does X say about Y?" fits any subject, so a likeness to the document examples proves nothing.
    # A real document question has a document word, which the keyword check above already accepts.
    sem_hits = [h for h in sem_hits if h["tool"] != "search_clauses"]
    if sem_hits:
        top = sem_hits[0]
        checks.append(_guard(
            "scope",
            f"Semantic match to RiskSphere intent '{top['intent']}' (score {top['score']}, mode {top['mode']}).",
            True,
        ))
        return _finish_guards(checks)
    checks.append(_guard(
        "scope",
        "Looked for a desk word, a counterparty, or a semantic tool match. None matched, so it is out of scope.",
        False,
    ))
    return _finish_guards(checks)


_TOOL_TO_INTENT = {
    "search_clauses": "document_search",
    "current_exposure": "current_exposure",
    "check_pfe_limit": "future_exposure",
    "calculate_margin_call": "margin_call",
    "get_market_data": "market_data",
    "regulatory_haircut": "regulatory_haircut",
}
_INTENT_TO_TOOL = {v: k for k, v in _TOOL_TO_INTENT.items()}
_ALWAYS_REQUIRED = {
    "current_exposure": "counterparty",
    "check_pfe_limit": "counterparty",
    "calculate_margin_call": "counterparty",
    "get_market_data": "ticker",
    "regulatory_haircut": "asset_class",
}


def required_arg(step):
    """The one argument a planned step must have before it can run, or None.

    Every rule is here. Most tools always need the same argument (_ALWAYS_REQUIRED).
    The document search is the exception: its query is always the user's own words, so it
    is never asked for, and a counterparty is needed only for the agreement pile, because
    each agreement belongs to one name while the haircut schedules apply to everyone.
    step is {"tool", "args"}.
    """
    tool = step["tool"]
    if tool == "search_clauses":
        return "counterparty" if (step.get("args") or {}).get("pile") == "agreement" else None
    return _ALWAYS_REQUIRED.get(tool)
_TICKER_RE = re.compile(r"\b(SPY|SHY|LQD|EURUSD=X|QQQ|GLD|TLT)\b", re.I)

# A question about what a document says, as opposed to a question about a number.
# "What is Meridian's threshold?" is a calculation. "What does the Meridian CSA say
# about the threshold?" asks for the wording, so it goes to the document search.
_DOC_WORDS_RE = re.compile(r"\b(clauses?|paragraphs?|wording|verbatim)\b", re.I)
_DOC_ASK_RE = re.compile(
    r"\b(says?|said|states?|stipulat\w+|provid(es?)|written|quote the|cite|according to|per the|spell(s)? out)\b", re.I
)
_DOC_NOUN_RE = re.compile(
    r"\b(csa|credit support (annex|agreement)|agreement|schedule|annex|document|regulation|cfr|d499|bcbs|basel|"
    r"table|rule)\b",
    re.I,
)
_SCHEDULE_NAMED_RE = re.compile(r"\b(cfr|d499|bcbs|basel|table b)\b|haircut schedule|discount schedule", re.I)
_AGREEMENT_NAMED_RE = re.compile(r"\b(csa|agreement|credit support)\b", re.I)
_SCHEDULE_TOPIC_RE = re.compile(
    r"haircut|discount|schedule|s&p|equit|\bgold\b|government (bond|debt)|\bgse\b|publicly traded", re.I
)


def is_document_question(question):
    """True when the question asks what a document says, not for a calculated figure."""
    if _DOC_WORDS_RE.search(question):
        return True
    return bool(_DOC_ASK_RE.search(question) and _DOC_NOUN_RE.search(question))


def infer_pile(question):
    """Which pile a document question belongs to: "haircut_schedule" or "agreement".

    A named regulation or table wins, then a named agreement, then the topic words.
    Python decides this, so a model that picks the wrong pile does not change the answer.
    """
    if _SCHEDULE_NAMED_RE.search(question):
        return "haircut_schedule"
    if _AGREEMENT_NAMED_RE.search(question):
        return "agreement"
    if _SCHEDULE_TOPIC_RE.search(question):
        return "haircut_schedule"
    return "agreement"


def _norm_text(text):
    return re.sub(r"\s+", " ", text.lower()).strip()


def quoted_strings(text):
    """Every stretch of text inside double quotes that is long enough to be a quotation."""
    return [a or b for a, b in re.findall(r'"([^"]{8,})"|\u201c([^\u201d]{8,})\u201d', text)]


def quote_ok(quote, passages):
    """True when the quotation appears word for word in one of the passages.

    Case and spacing are ignored, and so are the full stop or comma at the ends.
    This is the check behind the rule that a quoted sentence stays only when the
    quote is in the passage.
    """
    needle = _norm_text(quote).strip(" .,;:")
    return bool(needle) and any(needle in _norm_text(p) for p in passages)


def _stated_buckets(question):
    """Maturity and rating buckets the question states in words. Only stated ones are returned."""
    lowered = question.lower()
    out = {}
    if re.search(r"over 5|more than 5|>\s*5|beyond 5|after 5|long.dated|over five|more than five", lowered):
        out["maturity_bucket"] = ">5y"
    if re.search(r"under (a |one |1)|less than (a |one |1)|<=\s*1|within (a |one |1)|up to (a |one |1)|short.dated|(a|one) year or less", lowered):
        out["maturity_bucket"] = "<=1y"
    if re.search(r"1 to 5|one to five|1-5|between 1 and 5", lowered):
        out["maturity_bucket"] = "1-5y"
    if re.search(r"a\+|bbb|\ba to b|\ba-rated", lowered):
        out["rating_bucket"] = "A+-BBB-"
    elif re.search(r"aaa|\baa\b", lowered):
        out["rating_bucket"] = "AAA-AA-"
    return out


def _extract_haircut_args(question):
    """Extract haircut asset_class, maturity_bucket, and rating_bucket from text."""
    lowered = question.lower()
    asset = ""
    if re.search(r"equit(y|ies)|shares|stocks?", lowered):
        other = re.search(r"\bother\b|non-?index|not in (a |the )?(main )?index|outside (the |a )?(main )?index", lowered)
        asset = "equity_other" if other else "equity_main_index"
    else:
        for name in ("gold", "corporate", "government", "cash"):
            if name in lowered:
                asset = name
                break
    args = {"asset_class": asset, "maturity_bucket": "1-5y", "rating_bucket": "AAA-AA-"}
    args.update(_stated_buckets(question))
    return args


def _normalize_step(step, cfg):
    """Turn one planned step into a tool name plus clean arguments.

    Missing required arguments (counterparty, ticker, or asset_class) are
    kept as "" so _fill_last or check_missing_args can resolve or clarify them.
    Raises ValueError for an unknown tool, invalid asset_class, or bad shape.
    """
    if not isinstance(step, dict):
        raise ValueError("a step must be an object")
    tool = str(step.get("tool", "")).strip()
    if tool not in _TOOL_TO_INTENT:
        raise ValueError(f"unknown tool {tool}")
    args = step.get("args") or {}
    if not isinstance(args, dict):
        raise ValueError("args must be an object")
    if tool in ("current_exposure", "calculate_margin_call", "check_pfe_limit"):
        raw_cid = str(args.get("counterparty", "")).strip()
        cid = books.find_party(books.names_only(), raw_cid) if raw_cid else ""
        args = {"counterparty": cid}
    elif tool == "get_market_data":
        ticker = str(args.get("ticker", "")).strip().upper()
        args = {"ticker": ticker}
    elif tool == "search_clauses":
        raw_cid = str(args.get("counterparty", "")).strip()
        cid = books.find_party(books.names_only(), raw_cid) if raw_cid else ""
        pile = str(args.get("pile") or "").strip()
        if pile not in cfg.get("piles", ["agreement", "haircut_schedule"]):
            pile = ""
        args = {"query": str(args.get("query") or "").strip(), "pile": pile, "counterparty": cid}
    else:
        asset = str(args.get("asset_class", "")).strip()
        if asset and asset not in cfg["asset_classes"]:
            log.debug(f"planner gave asset_class {asset!r}, not in the table; blanked")
            asset = ""  # ground_args fills it from the question, or check_missing_args asks for it
        maturity = str(args.get("maturity_bucket") or "1-5y")
        rating = str(args.get("rating_bucket") or "AAA-AA-")
        if maturity not in cfg["maturity_buckets"]:
            maturity = "1-5y"
        if rating not in cfg["rating_buckets"]:
            rating = "AAA-AA-"
        args = {"asset_class": asset, "maturity_bucket": maturity, "rating_bucket": rating}
    return {"tool": tool, "args": args}


def _normalize_intents(raw_intent):
    """Normalize plan intent into a list of clean lowercase strings."""
    if isinstance(raw_intent, list):
        items = [english_only(str(x)).lower() for x in raw_intent if str(x).strip()]
    elif raw_intent:
        items = [english_only(str(raw_intent)).lower()]
    else:
        items = []
    return [x for x in items if x] or ["unspecified"]


def validate_plan(plan, cfg):
    """Accept a planner object or raise ValueError.

    Requires a steps list of at most max_steps (default 4). Strips non-English
    characters from the reason fields. A refuse flag clears the steps.
    When intent names canonical intents, verifies they match the tools in steps.
    """
    if not isinstance(plan, dict) or not isinstance(plan.get("steps"), list):
        raise ValueError("plan must be an object with a steps list")
    if len(plan["steps"]) > cfg.get("max_steps", 4):
        raise ValueError("at most 4 steps")
    plan["refuse"] = bool(plan.get("refuse"))
    plan["refusal_reason"] = english_only(str(plan.get("refusal_reason") or ""))
    plan["reason"] = english_only(str(plan.get("reason") or ""))
    intents = _normalize_intents(plan.get("intent"))
    plan["intent"] = intents
    if plan["refuse"]:
        plan["steps"] = []
        return plan
    if not plan["steps"]:
        raise ValueError("plan has no steps")
    plan["steps"] = [_normalize_step(step, cfg) for step in plan["steps"]]
    canonical = [name for name in intents if name in _INTENT_TO_TOOL]
    if canonical and len(canonical) == len(intents):
        expected_tools = {_INTENT_TO_TOOL[name] for name in canonical}
        actual_tools = {step["tool"] for step in plan["steps"]}
        if expected_tools != actual_tools:
            raise ValueError(f"intent {intents} does not match tools {sorted(actual_tools)}")
    return plan


def check_missing_args(plan, cfg):
    """If any planned step is missing its required arg, return a clarification plan."""
    if plan.get("refuse"):
        return plan
    clarifications = cfg.get("clarifications") or {}
    for step in plan.get("steps") or []:
        tool = step["tool"]
        req = required_arg(step)
        if req and not step["args"].get(req):
            msg = clarifications.get(req) or f"Please specify {req} for {tool}."
            return {
                "intent": [_TOOL_TO_INTENT[tool]],
                "refuse": False,
                "refusal_reason": "",
                "reason": f"need {req} before running {tool}",
                "steps": [],
                "needs_clarification": True,
                "missing_arg": req,
                "pending_tool": {"tool": tool, "missing_arg": req, "args": step["args"]},
                "clarification_message": msg,
                "router": plan.get("router", "rules"),
            }
    return plan


def resolve_pending(question, state):
    """If session state has a pending_tool, try to fill its missing arg from question."""
    pending = state.get("pending_tool")
    if not isinstance(pending, dict) or not pending.get("tool"):
        return None
    cfg = load_config()
    tool = pending["tool"]
    args = dict(pending.get("args") or {})
    req = pending.get("missing_arg") or required_arg({"tool": tool, "args": args})
    if req == "counterparty":
        cid = books.find_party(books.names_only(), question)
        if not cid:
            return None
        args["counterparty"] = cid
    elif req == "ticker":
        m = _TICKER_RE.search(question)
        if not m:
            return None
        args["ticker"] = m.group(1).upper()
    elif req == "asset_class":
        extracted = _extract_haircut_args(question)
        if not extracted["asset_class"]:
            return None
        args.update(extracted)
    else:
        return None
    plan = {
        "intent": [_TOOL_TO_INTENT[tool]],
        "refuse": False,
        "refusal_reason": "",
        "reason": f"resolved missing {req} from follow-up",
        "steps": [{"tool": tool, "args": args}],
        "router": "clarification_followup",
    }
    return validate_plan(plan, cfg)


def _fill_last(question, plan, state):
    """Fill a blank counterparty from the question, then from the session."""
    last = state.get("last_counterparty") or ""
    named = books.find_party(books.names_only(), question)
    follow = bool(re.search(r"\b(it|its|their|them)\b", question.lower()))
    for step in plan.get("steps") or []:
        if "counterparty" not in step["args"]:
            continue
        if step["args"]["counterparty"]:
            continue
        step["args"]["counterparty"] = named or (last if follow else "")
    return plan


def _mentioned(cid, question):
    """True when counterparty id cid is named in the question (id, full name, or first word)."""
    lowered = question.lower()
    for row in books.names_only():
        if row["counterparty_id"] == cid:
            first = row["name"].split()[0].lower()
            return cid in lowered or row["name"].lower() in lowered or first in lowered
    return False


def ground_args(question, plan, state):
    """Blank any counterparty or ticker the model supplied that the question does not support.

    A counterparty is kept only when the question names it, or when the question says
    it/its/their/them and the session remembers that name. A ticker is kept only when
    the symbol appears in the question. Blank values become a clarification request
    through check_missing_args. The model's "do not guess" instruction is not enough on
    its own, so Python enforces it.
    """
    last = state.get("last_counterparty") or ""
    follow = bool(re.search(r"\b(it|its|their|them)\b", question.lower()))
    if is_document_question(question) and not any(s["tool"] == "search_clauses" for s in plan.get("steps") or []):
        if plan.get("steps") and not plan.get("refuse"):
            named = books.find_party(books.names_only(), question)
            plan["steps"] = [{"tool": "search_clauses", "args": {
                "query": question, "pile": infer_pile(question), "counterparty": named or (last if follow else ""),
            }}]
            plan["intent"] = ["document_search"]
            plan["reason"] = "the question asks what a document says, so it goes to the document search"
    for step in plan.get("steps") or []:
        args = step["args"]
        if step["tool"] == "search_clauses":
            args["query"] = question  # the search runs on the user's words, never on a model rewrite
            args["pile"] = infer_pile(question)
        cid = args.get("counterparty")
        if cid and not (_mentioned(cid, question) or (follow and cid == last)):
            args["counterparty"] = ""
        ticker = args.get("ticker")
        if ticker and ticker.lower() not in question.lower():
            args["ticker"] = ""
        if step["tool"] == "regulatory_haircut":
            stated = _extract_haircut_args(question)["asset_class"]
            if stated and (not args.get("asset_class") or
                           (stated.startswith("equity") and args["asset_class"].startswith("equity"))):
                args["asset_class"] = stated  # "other equities" is not the main index, whatever the model said
            args.update(_stated_buckets(question))  # buckets stated in words win over the model's guess
    named = books.find_party(books.names_only(), question)
    if named and not is_document_question(question):
        for i, step in enumerate(plan.get("steps") or []):
            if step["tool"] == "regulatory_haircut":
                # A haircut on a named counterparty's collateral is part of its margin call (README).
                plan["steps"][i] = {"tool": "calculate_margin_call", "args": {"counterparty": named}}
        tools = []
        for step in plan.get("steps") or []:
            if step not in tools:
                tools.append(step)
        plan["steps"] = tools
        plan["intent"] = [_TOOL_TO_INTENT[s["tool"]] for s in plan["steps"]] or plan.get("intent")
    return plan


def heuristic_plan(question, state):
    """Choose tools from keywords or semantic exemplars when the model is off.

    If a tool matches but its required argument (counterparty, ticker, or
    asset_class) is missing, returns a clarification request instead of guessing.
    """
    cfg = load_config()
    lowered = question.lower()
    named = books.find_party(books.names_only(), question)
    follow = bool(re.search(r"\b(it|its|their|them)\b", lowered))
    cid = named or (state.get("last_counterparty") if follow else "") or ""
    steps = []
    if is_document_question(question):
        steps.append({"tool": "search_clauses", "args": {
            "query": question, "pile": infer_pile(question), "counterparty": cid,
        }})
        plan = {
            "intent": ["document_search"],
            "refuse": False,
            "refusal_reason": "",
            "reason": "keyword plan, model was not called. The question asks what a document says.",
            "steps": steps,
            "router": "rules",
        }
        return check_missing_args(validate_plan(plan, cfg), cfg)
    if re.search(r"haircut|discount|knocked off|percentage (is )?(taken )?off", lowered) and not named:
        steps.append({"tool": "regulatory_haircut", "args": _extract_haircut_args(question)})
    ticker = _TICKER_RE.search(question)
    if (ticker and re.search(r"price|spot|vol|yahoo|quote|trading|close|rate|choppy|moved?", lowered)) or (
        re.search(r"\b(price|spot|volatility|yahoo|quote)\b", lowered) and not named
    ):
        steps.append({"tool": "get_market_data", "args": {"ticker": ticker.group(1).upper() if ticker else ""}})
    wants_margin = bool(re.search(
        r"margin|\bcall\b(?! with)|threshold|mta|minimum transfer|delivery|deliver|collateral requirement|credit support|"
        r"(more|additional|extra) collateral|ask .{0,25}collateral|"
        r"\bcsa\b|\bowe\b|\bowed\b|top-?up|collateral (back|due|required|to post)|return of collateral|"
        r"(post|posted|post)\b.{0,20}collateral|\bhaircut\b", lowered)) if (named or cid) else bool(
        re.search(r"margin|\bcall\b(?! with)|threshold|mta|minimum transfer|delivery|credit support|collateral requirement", lowered))
    wants_pfe = bool(re.search(
        r"\bpfe\b|future exposure|potential future|forward.looking|simulat\w+|peak (exposure|pfe)|"
        r"could .{0,20}(peak|get to)|over the (life|next|coming)|percentile", lowered))
    asks_today = bool(re.search(r"\btoday|current(ly)?|right now|mark.to.market|where are we", lowered))
    wants_exposure = bool(re.search(r"exposure|limit|breach|headroom|mark", lowered)) and (not wants_pfe or asks_today)
    if wants_pfe:
        steps.append({"tool": "check_pfe_limit", "args": {"counterparty": cid}})
    if wants_margin:
        steps.append({"tool": "calculate_margin_call", "args": {"counterparty": cid}})
    if wants_exposure and not any(step["tool"] == "current_exposure" for step in steps):
        steps.append({"tool": "current_exposure", "args": {"counterparty": cid}})
    if cid and not steps:
        steps.append({"tool": "current_exposure", "args": {"counterparty": cid}})

    reason = "keyword plan, model was not called"
    if not steps:
        import search
        sem_hits = search.match_intents(question)
        if sem_hits:
            top = sem_hits[0]
            tool = top["tool"]
            if tool == "regulatory_haircut":
                args = _extract_haircut_args(question)
            elif tool == "get_market_data":
                args = {"ticker": ticker.group(1).upper() if ticker else ""}
            elif tool == "search_clauses":
                args = {"query": question, "pile": infer_pile(question), "counterparty": cid}
            else:
                args = {"counterparty": cid}
            steps.append({"tool": tool, "args": args})
            reason = f"semantic match to '{top['intent']}' (score {top['score']}, mode {top['mode']})"

    intents = [_TOOL_TO_INTENT[s["tool"]] for s in steps] if steps else ["refusal"]
    plan = {
        "intent": intents,
        "refuse": False,
        "refusal_reason": "",
        "reason": reason,
        "steps": steps,
        "router": "rules",
    }
    if not steps:
        plan["refuse"] = True
        plan["refusal_reason"] = "no tool matched"
        return plan
    validated = validate_plan(plan, cfg)
    return check_missing_args(validated, cfg)


def plan_with_model(question, state):
    """Ask Qwen for one JSON plan, validate it, and check required arguments."""
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
    )
    log.debug("planner prompt " + prompt)
    with _LOCK:
        raw = chat(prompt, max_tokens=cfg["plan_max_tokens"])
    plan = validate_plan(_json_object(raw), cfg)
    plan = _fill_last(question, plan, state)
    plan = ground_args(question, plan, state)
    if any(s["tool"] == "search_clauses" for s in plan.get("steps") or []) and not is_document_question(question):
        # The model picked the document search for a question that asks for a figure. Python decides
        # what a document question is, so the keyword plan answers instead of a quoted passage.
        fallback = heuristic_plan(question, state)
        fallback["router"] = "qwen3-8b"
        fallback["reason"] = "model chose the document search for a question that is not about wording; keyword plan used"
        fallback["prompt"] = prompt
        return fallback
    plan["router"] = "qwen3-8b"
    plan["prompt"] = prompt
    return check_missing_args(plan, cfg)


def check_answer(question, hits):
    """Ask Qwen whether one of the retrieved passages answers the question. One model call.

    This replaces the wording call for a document question: the reply is not shown to the
    user, it only picks a passage or rejects them all. hits is the search tool's list
    (id, text, ...). Returns {"checked", "answers", "passage_id", "error"}:
      checked False  -> the model could not be asked, or its reply was unusable (bad JSON,
                        a missing field, or an id that was not in the list). The caller keeps
                        the word-rule result and says the answer was not checked.
      answers False  -> no passage answers the question.
      answers True   -> passage_id is one of the ids that were sent.
    The passages are document text; the reply is limited to a yes/no and an id from the list,
    so text inside a passage cannot make the model say anything that reaches the user.
    """
    cfg = load_config()
    ids = [h["id"] for h in hits]
    passages = "\n".join(f"[{h['id']}] {h['text']}" for h in hits)
    prompt = fill_prompt(cfg["answer_check_prompt"], question=question, passages=passages)
    out = {"checked": False, "answers": False, "passage_id": "", "error": ""}
    try:
        with _LOCK:
            raw = chat(prompt, max_tokens=cfg.get("answer_check_max_tokens", 120))
        reply = _json_object(raw)
    except Exception as exc:
        out["error"] = f"answer check failed: {exc}"
        return out
    answers = reply.get("answers")
    pid = str(reply.get("passage_id") or "").strip()
    if not isinstance(answers, bool):
        out["error"] = "answer check reply has no true/false answers field"
        return out
    if answers and pid not in ids:
        out["error"] = f"answer check named passage {pid!r}, which was not in the list"
        return out
    out.update(checked=True, answers=answers, passage_id=pid if answers else "")
    return out


def compose(question, tool_result, issues=None):
    """Ask Qwen to phrase a result that Python already computed.

    tool_result is the list of successful tool rows. issues, when set, is
    the critic's complaint from the previous draft, appended so the rewrite
    does not add new figures. Returns (sentences, prompt). The sentences
    have CJK characters removed. Does not change any number.
    """
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
    issues = []
    blob = json.dumps(tool_result)
    allowed = set(re.findall(r"\d{3,}", blob))
    allowed |= {n.replace(",", "") for n in re.findall(r"\d{1,3}(?:,\d{3})+", blob)}  # 1,000,000 in a quoted passage
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
        if row.get("tool") == "search_clauses":
            passages = [h["text"] for h in output.get("hits") or []]
            quotes = quoted_strings(text)
            if passages and not quotes:
                issues.append("no passage is quoted")
            for quote in quotes:
                if not quote_ok(quote, passages):
                    issues.append("quote is not in the passage: " + quote[:50])
            if not passages and not re.search(r"no passage|nothing|not found|no match|did not find|could not find", low):
                issues.append("wording does not say that no passage was found")
    ran = {row.get("tool") for row in tool_result}
    if "calculate_margin_call" not in ran and re.search(r"margin call|no margin|no call", low):
        issues.append("wording talks about a margin call but that tool did not run")
    if "calculate_margin_call" not in ran and re.search(r"before any collateral", low):
        issues.append("wording says collateral must be reviewed but no margin amount was calculated")
    return issues
