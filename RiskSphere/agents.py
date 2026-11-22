"""Two agents, and the Python services behind the calculations.

The orchestrator plans and phrases. It calls the chat model for the plan
and the wording. Documents is the second agent. It retrieves clauses, calls
the chat model once to propose a term from those passages, and writes a
draft only after Python accepts the quote. It does not compute a risk figure.

Every calculation is a Python service. A service does not plan, does not
phrase, and does not call the model. The log names the agent or the service
so we can see which code ran. This file is the map, so the write-up, the
console log, and the dispatcher stay on the same list.

Nothing here computes a figure.
"""

# name -> (role, tools). Documents owns the documentation tools.
AGENT = {
    "Orchestrator": (
        "Guards the question, plans the steps, writes the wording, and runs the critic. "
        "Calls the chat model for the plan and the wording.",
        [],
    ),
    "Documents": (
        "Retrieves clauses, asks the chat model to propose one term from those passages, "
        "and writes a draft only after the quote check passes.",
        ["search_clauses", "propose_term"],
    ),
}

# Service name -> (what the Python does, tools it owns). Not agents.
SERVICES = {
    "Data quality": (
        "Checks ids, dates, units, and signs before any new figure. A bad row stops the run.",
        ["check_data_quality"],
    ),
    "Market": (
        "Yahoo prices, one-year volatility, scenario profit and loss, and the stress book.",
        ["get_market_data", "scenario_pnl", "draft_stress_book"],
    ),
    "Simulation": (
        "Correlated Monte Carlo for the simulated potential future exposure, with engine checks.",
        ["check_pfe_limit"],
    ),
    "Margin": (
        "Variation margin from the agreement, an initial-margin proxy, and the draft notice.",
        ["calculate_margin_call", "initial_margin_proxy", "draft_margin_notice"],
    ),
    "Collateral": (
        "The haircut schedule, eligible value, shortfall, and surplus.",
        ["regulatory_haircut", "collateral_coverage"],
    ),
    "Exposure": (
        "Today's exposure, expected loss, alerts, the dispute row, and the consolidated report.",
        ["current_exposure", "expected_loss", "list_alerts", "draft_dispute", "draft_report"],
    ),
}

TOOL_OWNER = {}
for _name, (_role, _tools) in list(AGENT.items()) + list(SERVICES.items()):
    for _tool in _tools:
        TOOL_OWNER[_tool] = _name


def agent_for(tool_name):
    """Return the agent or Python service that owns a tool."""
    return TOOL_OWNER.get(tool_name, "Orchestrator")


def owned_tools():
    """Every tool an agent or a Python service owns, in roster order."""
    tools = []
    for _name, (_role, owner_tools) in list(AGENT.items()) + list(SERVICES.items()):
        tools.extend(owner_tools)
    return tools


def describe():
    """The two agents, as {agent, role, tools}, for a report or the console."""
    return [
        {"agent": name, "role": role, "tools": list(tools)}
        for name, (role, tools) in AGENT.items()
    ]


def services():
    """Python services, as {service, role, tools}. These are not agents."""
    return [
        {"service": name, "role": role, "tools": list(tools)}
        for name, (role, tools) in SERVICES.items()
    ]
