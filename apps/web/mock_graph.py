from __future__ import annotations

from urllib.parse import urlparse

SAMPLE_POLYMARKET_LINKS = [
    {
        "url": "https://polymarket.com/event/fed-decision-in-march-885",
        "label": "Fed decision in March",
        "caption": "Rates, macro spillover, and adjacent policy contracts",
    },
    {
        "url": "https://polymarket.com/event/will-crude-oil-cl-hit-by-end-of-march",
        "label": "Crude oil by end of March",
        "caption": "Commodity repricing and second-order macro effects",
    },
    {
        "url": "https://polymarket.com/event/democratic-presidential-nominee-2028",
        "label": "Democratic nominee 2028",
        "caption": "Political coalition shifts and long-horizon narrative propagation",
    },
]

SAMPLE_POLYMARKET_URLS = [item["url"] for item in SAMPLE_POLYMARKET_LINKS]

NODE_TYPE_OPTIONS = [
    {"value": "Event", "label": "Event", "css_class": "event"},
    {"value": "Entity", "label": "Entity", "css_class": "entity"},
    {"value": "RelatedMarket", "label": "Related market", "css_class": "related-market"},
    {"value": "Evidence", "label": "Evidence", "css_class": "evidence"},
    {"value": "Rule", "label": "Rule", "css_class": "rule"},
    {"value": "Hypothesis", "label": "Hypothesis", "css_class": "hypothesis"},
]

LAYOUT_OPTIONS = [
    {"value": "cose", "label": "Adaptive force"},
    {"value": "concentric", "label": "Signal rings"},
    {"value": "breadthfirst", "label": "Impact flow"},
]

DEFAULT_CONTROLS = {
    "showEdgeLabels": False,
    "confidenceThreshold": 0.45,
    "layout": "concentric",
    "nodeFilters": {item["value"]: True for item in NODE_TYPE_OPTIONS},
}

TOKEN_REWRITES = {
    "opec": "OPEC+",
    "q2": "Q2",
    "q3": "Q3",
    "q4": "Q4",
    "us": "US",
    "cpi": "CPI",
    "vlcc": "VLCC",
    "eia": "EIA",
    "95": "$95",
    "2026": "2026",
}


def _extract_slug(url: str) -> str:
    path = urlparse(url).path.strip("/")
    parts = [part for part in path.split("/") if part]
    return parts[-1] if parts else "energy-market-preview"


def _infer_event_title(url: str) -> str:
    slug = _extract_slug(url)
    if "fed-decision-in-march" in slug:
        return "Fed decision in March?"
    if "crude-oil-cl-hit-by-end-of-march" in slug:
        return "Will Crude Oil (CL) hit its target by end of March?"
    if "democratic-presidential-nominee-2028" in slug:
        return "Democratic Presidential Nominee 2028"
    if "brent" in slug:
        return "Will Brent crude trade above $95 before July 2026?"
    if "opec" in slug:
        return "Will OPEC+ extend output cuts through Q3 2026?"
    if "strait-of-hormuz" in slug:
        return "Will the Strait of Hormuz face a major shipping disruption in 2026?"
    if "wti-crude" in slug:
        return "Will WTI crude trade above $90 in Q2 2026?"
    if "cpi-reaccelerate" in slug:
        return "Will US CPI reaccelerate above 3.5% in 2026?"
    if "vlcc-spot-rates" in slug:
        return "Will VLCC spot rates break 2025 highs in 2026?"

    words = []
    for token in slug.replace("-", " ").split():
        words.append(TOKEN_REWRITES.get(token.lower(), token.capitalize()))
    sentence = " ".join(words).strip() or "Energy market preview"
    return sentence[0].upper() + sentence[1:]


def _infer_tags(url: str) -> list[str]:
    slug = _extract_slug(url)
    if "fed-decision-in-march" in slug:
        return ["macro", "rates", "fed", "policy"]
    if "crude-oil-cl-hit-by-end-of-march" in slug:
        return ["energy", "commodities", "oil", "macro"]
    if "democratic-presidential-nominee-2028" in slug:
        return ["politics", "elections", "democrats", "us-politics"]
    if "opec" in slug:
        return ["energy", "macro", "commodities", "cartel-policy"]
    if "strait-of-hormuz" in slug:
        return ["energy", "shipping", "geopolitics", "middle-east"]
    if "wti-crude" in slug:
        return ["energy", "oil", "us-markets", "benchmarks"]
    if "cpi-reaccelerate" in slug:
        return ["macro", "inflation", "rates", "energy"]
    if "vlcc-spot-rates" in slug:
        return ["shipping", "freight", "energy", "logistics"]
    return ["energy", "macro", "oil", "shipping"]


def _metadata(*pairs: tuple[str, str]) -> list[dict[str, str]]:
    return [{"label": label, "value": value} for label, value in pairs]


def build_mock_graph_payload(source_url: str) -> dict:
    event_title = _infer_event_title(source_url)
    event_tags = _infer_tags(source_url)

    nodes = [
        {
            "id": "evt_001",
            "label": event_title,
            "type": "Event",
            "confidence": 1.0,
            "summary": "Primary market event anchored to oil supply, transit risk, and macro demand feedback loops.",
            "metadata": _metadata(
                ("Resolution source", "ICE Brent front-month settlement"),
                ("Time horizon", "Through the close of June 30, 2026"),
                ("Core drivers", "OPEC policy, Gulf shipping flow, macro demand"),
            ),
            "evidence_snippets": [],
        },
        {
            "id": "ent_opec",
            "label": "OPEC+ supply policy",
            "type": "Entity",
            "confidence": 0.89,
            "summary": "The cartel remains the cleanest direct lever on prompt crude supply and trader positioning.",
            "metadata": _metadata(
                ("Signal", "Quota guidance and compliance"),
                ("Why it matters", "Any surprise extension tightens the forward curve"),
            ),
            "evidence_snippets": [],
        },
        {
            "id": "ent_iran",
            "label": "Iran risk premium",
            "type": "Entity",
            "confidence": 0.78,
            "summary": "Regional escalation can widen insurance premia and push physical cargoes onto longer routes.",
            "metadata": _metadata(
                ("Signal", "Sanctions and naval posture"),
                ("Why it matters", "Supply expectations move before barrels disappear"),
            ),
            "evidence_snippets": [],
        },
        {
            "id": "ent_shipping",
            "label": "Gulf shipping insurers",
            "type": "Entity",
            "confidence": 0.74,
            "summary": "Insurance repricing is an early marker for whether a headline becomes an actual logistics shock.",
            "metadata": _metadata(
                ("Signal", "War-risk surcharge changes"),
                ("Why it matters", "Insurance can be the constraint before vessels stop sailing"),
            ),
            "evidence_snippets": [],
        },
        {
            "id": "ent_refiners",
            "label": "Asian refinery demand",
            "type": "Entity",
            "confidence": 0.66,
            "summary": "Refiner restocking can turn a short-lived squeeze into a durable price move.",
            "metadata": _metadata(
                ("Signal", "Import margins and utilization"),
                ("Why it matters", "Demand stickiness changes how fast prices mean-revert"),
            ),
            "evidence_snippets": [],
        },
        {
            "id": "mkt_wti",
            "label": "WTI above $90 in Q2 2026",
            "type": "RelatedMarket",
            "confidence": 0.88,
            "source_url": "https://polymarket.com/event/will-wti-crude-trade-above-90-in-q2-2026",
            "summary": "US crude benchmarks often reprice in sympathy, even when the initial catalyst is ex-US.",
            "metadata": _metadata(
                ("Market link", "High correlation during supply scares"),
                ("Portfolio use", "Cross-market confirmation"),
            ),
            "evidence_snippets": [],
        },
        {
            "id": "mkt_cpi",
            "label": "US CPI reaccelerates above 3.5%",
            "type": "RelatedMarket",
            "confidence": 0.71,
            "source_url": "https://polymarket.com/event/will-us-cpi-reaccelerate-above-3-point-5-in-2026",
            "summary": "Energy passes through to headline inflation and can alter macro market pricing.",
            "metadata": _metadata(
                ("Market link", "Energy-through-inflation channel"),
                ("Portfolio use", "Second-order macro hedge"),
            ),
            "evidence_snippets": [],
        },
        {
            "id": "mkt_tanker",
            "label": "VLCC spot rates break 2025 highs",
            "type": "RelatedMarket",
            "confidence": 0.84,
            "source_url": "https://polymarket.com/event/will-vlcc-spot-rates-break-2025-highs-in-2026",
            "summary": "Shipping stress can show up in tanker pricing even before it fully clears into crude.",
            "metadata": _metadata(
                ("Market link", "Freight as an early stress indicator"),
                ("Portfolio use", "Operational confirmation"),
            ),
            "evidence_snippets": [],
        },
        {
            "id": "ev_eia",
            "label": "EIA inventory draw streak",
            "type": "Evidence",
            "confidence": 0.82,
            "summary": "Consecutive draws suggest the market has less buffer if a transit shock materializes.",
            "metadata": _metadata(
                ("Window", "Three weekly releases"),
                ("Interpretation", "Lower inventories increase convexity"),
            ),
            "evidence_snippets": [
                "Commercial crude inventories posted a third straight draw, compressing visible cover.",
                "Refined product balances remain tighter than the headline stock number suggests.",
            ],
        },
        {
            "id": "ev_ais",
            "label": "AIS rerouting signals",
            "type": "Evidence",
            "confidence": 0.86,
            "summary": "Shipping path changes are one of the earliest verifiable signs that risk is operational, not rhetorical.",
            "metadata": _metadata(
                ("Window", "72-hour vessel movement sample"),
                ("Interpretation", "Longer routes lift freight and delay cargo timing"),
            ),
            "evidence_snippets": [
                "Several tankers added precautionary loops south of the Gulf entrance during the latest risk flare-up.",
                "Transit time widened before any formal closure scenario was priced as base case.",
            ],
        },
        {
            "id": "ev_options",
            "label": "Brent call skew steepens",
            "type": "Evidence",
            "confidence": 0.79,
            "summary": "Options skew shows whether traders are paying up for upside tail protection.",
            "metadata": _metadata(
                ("Window", "Front two summer expiries"),
                ("Interpretation", "Upside convexity demand implies fear of gap moves"),
            ),
            "evidence_snippets": [
                "Near-dated call demand has outpaced put demand as summer delivery risk rises.",
            ],
        },
        {
            "id": "rule_settlement",
            "label": "Resolution note: ICE Brent settlement",
            "type": "Rule",
            "confidence": 0.97,
            "summary": "The market resolves from a public exchange settlement print rather than discretionary interpretation.",
            "metadata": _metadata(
                ("Source", "ICE Brent front-month official settlement"),
                ("Importance", "Reduces ambiguity on transient intraday spikes"),
            ),
            "evidence_snippets": [],
        },
        {
            "id": "hyp_supply",
            "label": "Localized disruption becomes global supply squeeze",
            "type": "Hypothesis",
            "confidence": 0.81,
            "summary": "Minor rerouting and insurance friction can amplify into a broad tightness signal across crude and freight.",
            "metadata": _metadata(
                ("Mechanism", "Transit delay plus precautionary stockpiling"),
                ("Signal to watch", "Freight stress widening ahead of spot crude"),
            ),
            "evidence_snippets": [],
        },
        {
            "id": "hyp_spr",
            "label": "Strategic reserve release caps upside",
            "type": "Hypothesis",
            "confidence": 0.58,
            "summary": "Political pressure could trigger a temporary policy response that limits market follow-through.",
            "metadata": _metadata(
                ("Mechanism", "Policy response offsets supply scare"),
                ("Signal to watch", "Executive messaging around gasoline prices"),
            ),
            "evidence_snippets": [],
        },
        {
            "id": "hyp_demand",
            "label": "China rebound keeps dip buyers active",
            "type": "Hypothesis",
            "confidence": 0.63,
            "summary": "Demand resilience can make the market less forgiving when risk headlines pull prices higher.",
            "metadata": _metadata(
                ("Mechanism", "Industrial restocking reinforces tightness"),
                ("Signal to watch", "Refinery run rates and import quotas"),
            ),
            "evidence_snippets": [],
        },
    ]

    edges = [
        {
            "id": "edge_evt_opec",
            "source": "evt_001",
            "target": "ent_opec",
            "type": "involves",
            "confidence": 0.89,
            "explanation": "Cartel supply guidance is a direct driver of whether prompt Brent can sustain a breakout.",
        },
        {
            "id": "edge_evt_iran",
            "source": "evt_001",
            "target": "ent_iran",
            "type": "involves",
            "confidence": 0.78,
            "explanation": "Regional risk widens the geopolitical premium even before physical output changes.",
        },
        {
            "id": "edge_evt_shipping",
            "source": "evt_001",
            "target": "ent_shipping",
            "type": "involves",
            "confidence": 0.74,
            "explanation": "Insurance pricing is a leading operational indicator for whether transit risk is becoming real.",
        },
        {
            "id": "edge_evt_refiners",
            "source": "evt_001",
            "target": "ent_refiners",
            "type": "involves",
            "confidence": 0.66,
            "explanation": "Refinery demand changes how persistent any upside move becomes after the initial shock.",
        },
        {
            "id": "edge_evt_eia",
            "source": "evt_001",
            "target": "ev_eia",
            "type": "supported_by",
            "confidence": 0.82,
            "explanation": "Inventory draws reduce the market's available shock absorber.",
        },
        {
            "id": "edge_evt_ais",
            "source": "evt_001",
            "target": "ev_ais",
            "type": "supported_by",
            "confidence": 0.86,
            "explanation": "Shipping reroutes are tangible evidence that headlines are changing logistics behavior.",
        },
        {
            "id": "edge_evt_options",
            "source": "evt_001",
            "target": "ev_options",
            "type": "supported_by",
            "confidence": 0.79,
            "explanation": "Options skew confirms that traders are paying for upside tail protection.",
        },
        {
            "id": "edge_evt_wti",
            "source": "evt_001",
            "target": "mkt_wti",
            "type": "related_to",
            "confidence": 0.88,
            "explanation": "WTI is the closest liquid sister market for confirming a broad crude repricing.",
        },
        {
            "id": "edge_evt_cpi",
            "source": "evt_001",
            "target": "mkt_cpi",
            "type": "related_to",
            "confidence": 0.71,
            "explanation": "A sustained oil breakout can feed into headline inflation expectations.",
        },
        {
            "id": "edge_evt_tanker",
            "source": "evt_001",
            "target": "mkt_tanker",
            "type": "related_to",
            "confidence": 0.84,
            "explanation": "Freight stress often accelerates alongside physical crude dislocation.",
        },
        {
            "id": "edge_evt_rule",
            "source": "evt_001",
            "target": "rule_settlement",
            "type": "governed_by_rule",
            "confidence": 0.97,
            "explanation": "Resolution depends on the official exchange settlement, not intraday volatility.",
        },
        {
            "id": "edge_shipping_supply",
            "source": "ent_shipping",
            "target": "hyp_supply",
            "type": "affects_directly",
            "confidence": 0.81,
            "explanation": "Insurance and rerouting friction are the most direct path from headline risk to real supply delay.",
        },
        {
            "id": "edge_iran_supply",
            "source": "ent_iran",
            "target": "hyp_supply",
            "type": "affects_directly",
            "confidence": 0.76,
            "explanation": "Escalation risk is the narrative bridge that can make traders take shipping constraints seriously.",
        },
        {
            "id": "edge_supply_tanker",
            "source": "hyp_supply",
            "target": "mkt_tanker",
            "type": "affects_directly",
            "confidence": 0.83,
            "explanation": "A supply squeeze should show up first in freight markets as routes lengthen and risk premia climb.",
        },
        {
            "id": "edge_supply_cpi",
            "source": "hyp_supply",
            "target": "mkt_cpi",
            "type": "affects_indirectly",
            "confidence": 0.72,
            "explanation": "If energy remains elevated long enough, the inflation market becomes the second-order spillover.",
        },
        {
            "id": "edge_refiners_demand",
            "source": "ent_refiners",
            "target": "hyp_demand",
            "type": "related_to",
            "confidence": 0.63,
            "explanation": "Persistent refinery appetite is the demand-side reason a rally can keep extending.",
        },
        {
            "id": "edge_demand_event",
            "source": "hyp_demand",
            "target": "evt_001",
            "type": "affects_indirectly",
            "confidence": 0.63,
            "explanation": "Demand resilience keeps buyers active on pullbacks and makes breakouts more durable.",
        },
        {
            "id": "edge_spr_event",
            "source": "hyp_spr",
            "target": "evt_001",
            "type": "affects_indirectly",
            "confidence": 0.58,
            "explanation": "Policy intervention is a plausible cap on upside if consumer fuel prices become politically toxic.",
        },
        {
            "id": "edge_options_wti",
            "source": "ev_options",
            "target": "mkt_wti",
            "type": "mentions",
            "confidence": 0.74,
            "explanation": "The options market often starts repricing nearby benchmarks in tandem.",
        },
        {
            "id": "edge_ais_tanker",
            "source": "ev_ais",
            "target": "mkt_tanker",
            "type": "mentions",
            "confidence": 0.77,
            "explanation": "Observed route changes are tightly linked to freight rate reactions.",
        },
        {
            "id": "edge_wti_cpi",
            "source": "mkt_wti",
            "target": "mkt_cpi",
            "type": "related_to",
            "confidence": 0.57,
            "explanation": "Broader crude strength is one transmission path into inflation pricing.",
        },
        {
            "id": "edge_opec_event",
            "source": "ent_opec",
            "target": "evt_001",
            "type": "affects_directly",
            "confidence": 0.84,
            "explanation": "Unexpected production restraint directly increases the odds of a durable Brent upside break.",
        },
    ]

    return {
        "event": {
            "id": "evt_001",
            "title": event_title,
            "source_url": source_url,
            "status": "open",
            "tags": event_tags,
            "outcomes": ["Yes", "No"],
            "updated_at": "2026-03-10T18:00:00Z",
        },
        "run": {
            "id": None,
            "mode": "mock-preview",
            "persistence": "ephemeral",
            "builder": "django.mock_graph.build_mock_graph_payload",
            "generated_at": "2026-03-10T18:00:00Z",
        },
        "graph": {
            "nodes": nodes,
            "edges": edges,
        },
    }
