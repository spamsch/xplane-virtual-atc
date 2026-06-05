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


# FIS (Flight Information Service) — information, never control. The key lessons:
# traffic info is advisory ("if observed"), there are no clearances, and the
# service is terminated when the pilot reaches controlled airspace / destination.
FIS_EXAMPLES = [
    (
        "Pilot checks in with type, position and altitude requesting a basic service",
        "Bremen Information, D-EIYD, Cessna 172, 5 miles north of Osnabrück VOR, "
        "2500 feet, VFR Bielefeld to Münster, request basic service.",
        "D-EIYD, Bremen Information, basic service, squawk 7000, report reaching "
        "any controlled airspace.",
    ),
    (
        "Give traffic information as INFORMATION, not an instruction — qualify it",
        "Bremen Information, D-EIYD, request traffic.",
        "D-EIYD, traffic believed to be in your area, 2 o'clock, 4 miles, "
        "crossing left to right, a light aircraft, altitude unknown.",
    ),
    (
        "Pilot reports a position — acknowledge, never instruct",
        "D-EIYD, overhead Osnabrück VOR, 2500 feet.",
        "D-EIYD, roger.",
    ),
    (
        "Nearing the destination's controlled airspace — terminate and hand off",
        "D-EIYD, request frequency change to Münster.",
        "D-EIYD, basic service terminated, squawk 7000, contact Münster Tower "
        "129.805, good day.",
    ),
]

# Uncontrolled aerodrome (AFIS / UNICOM self-announce). No clearances at all;
# the operator passes aerodrome information and the pilot decides.
UNCONTROLLED_EXAMPLES = [
    (
        "Pilot calls for departure information at an uncontrolled field — pass "
        "info only, the take-off decision is the pilot's",
        "Bielefeld Information, D-EIYD, Cessna 172 at the apron, request taxi "
        "and departure information runway 25.",
        "D-EIYD, Bielefeld Information, runway 25 in use, wind 250 degrees 6 "
        "knots, QNH 1015, no reported traffic, taxi at your discretion.",
    ),
    (
        "Pilot announces lining up — acknowledge, do NOT 'clear' them",
        "D-EIYD, lining up runway 25 for departure.",
        "D-EIYD, roger, no reported traffic.",
    ),
    (
        "Inbound pilot reports — give the field info and known traffic, no landing clearance",
        "Bielefeld Information, D-EIYD, 5 miles east, inbound for landing.",
        "D-EIYD, roger, runway 25 in use, QNH 1015, one in the circuit, report final.",
    ),
]


def _render(header: str, examples) -> str:
    out = [header]
    for situation, pilot, atc in examples:
        out.append(f"- {situation}:")
        out.append(f"    Pilot: {pilot}")
        out.append(f"    You:   {atc}")
    return "\n".join(out)


def render() -> str:
    """Render the control examples as a prompt block."""
    return _render(
        "Example exchanges — note WHAT the pilot says and respond ONLY to that. "
        "Do not pre-empt requests:",
        EXAMPLES,
    )


def render_fis() -> str:
    return _render(
        "Example FIS exchanges — you INFORM, you do not control. Never clear, "
        "never instruct, always qualify traffic:",
        FIS_EXAMPLES,
    )


def render_uncontrolled() -> str:
    return _render(
        "Example exchanges at an UNCONTROLLED aerodrome — pass information only; "
        "the pilot decides and acts at their own discretion:",
        UNCONTROLLED_EXAMPLES,
    )


def render_for(service_kind: str) -> str:
    """Examples appropriate to the service: control | fis | uncontrolled."""
    if service_kind == "fis":
        return render_fis()
    if service_kind == "uncontrolled":
        return render_uncontrolled()
    return render()
