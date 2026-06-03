"""
Few-shot example exchanges that teach the controller correct VFR phraseology —
and, just as important, *when* to say it. The number-one failure mode is
answering a request the pilot never made (e.g. issuing a taxi clearance on a
bare initial contact). These examples anchor that discipline.

Add situations to EXAMPLES; they are rendered into the ATC system prompt
(`atc/engine.py`). Taxiway designators are written as letters (T2, M, N) — the
TTS normaliser speaks them ("Mike", "November") at synthesis time.
"""

# (situation, pilot transmission, correct ATC reply)
EXAMPLES = [
    (
        "Bare initial contact with NO request — acknowledge only; do NOT issue "
        "any clearance, taxi, or instruction the pilot did not ask for",
        "Hannover Ground, D-EIYD.",
        "D-EIYD, Hannover Ground, pass your message.",
    ),
    (
        "Pilot passes a full taxi request — now give the taxi clearance",
        "Hannover Ground, D-EIYD, Cessna 172 at general aviation, VFR to "
        "Bielefeld, request taxi.",
        "D-EIYD, taxi to holding point runway 27R via T2, M, N, QNH 1013.",
    ),
    (
        "Pilot reports ready for departure at Tower",
        "Hannover Tower, D-EIYD, holding point N, runway 27R, ready for departure.",
        "D-EIYD, runway 27R, wind 270 degrees 8 knots, cleared for take-off.",
    ),
    (
        "Pilot only requests startup — approve startup; the taxi clearance comes "
        "later when they request taxi",
        "Hannover Ground, D-EIYD, request startup.",
        "D-EIYD, startup approved, QNH 1013, report ready to taxi.",
    ),
    (
        "Pilot wants to leave the zone / change frequency",
        "D-EIYD, request frequency change.",
        "D-EIYD, frequency change approved, squawk 7000, good day.",
    ),
    (
        "Pilot's read-back is correct — confirm, nothing more",
        "Taxi to holding point runway 27R via T2 M N, QNH 1013, D-EIYD.",
        "D-EIYD, read-back correct.",
    ),
]


def render() -> str:
    """Render the examples as a prompt block."""
    out = [
        "Example exchanges — note WHAT the pilot says and respond ONLY to that. "
        "Do not pre-empt requests:",
    ]
    for situation, pilot, atc in EXAMPLES:
        out.append(f"- {situation}:")
        out.append(f"    Pilot: {pilot}")
        out.append(f"    You:   {atc}")
    return "\n".join(out)
