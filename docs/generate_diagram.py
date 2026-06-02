#!/usr/bin/env python3
"""
Generate architecture + process PDF for X-Plane Virtual ATC.

Output: docs/architecture.pdf
Usage:  python3 docs/generate_diagram.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fpdf import FPDF

# ------------------------------------------------------------------ #
# Palette

DARK_BLUE   = (26, 82, 118)
MID_BLUE    = (41, 128, 185)
LIGHT_BLUE  = (214, 234, 248)
DARK_GREY   = (44, 62, 80)
MID_GREY    = (120, 135, 145)
LIGHT_GREY  = (244, 246, 247)
WHITE       = (255, 255, 255)
BLACK       = (0, 0, 0)
GREEN_DARK  = (27, 94, 32)
GREEN_MID   = (39, 174, 96)
GREEN_LIGHT = (232, 245, 233)
AMBER_DARK  = (175, 80, 0)
AMBER_LIGHT = (255, 243, 224)
PURPLE_DARK = (74, 20, 140)
PURPLE_LIGHT = (243, 229, 245)
BORDER      = (189, 195, 199)
BORDER_BLUE = (100, 150, 200)


# ------------------------------------------------------------------ #
# Helpers

_FONT_REG  = "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"
_FONT_BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
FONT = "Uni"   # internal name — avoids fpdf2's built-in Arial→Helvetica alias


class PDF(FPDF):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.add_font(FONT, "",  _FONT_REG)
        self.add_font(FONT, "B", _FONT_BOLD)

    def footer(self):
        self.set_y(-13)
        self.set_font(FONT, "", 7.5)
        self.set_text_color(*MID_GREY)
        self.cell(0, 8, f"X-Plane Virtual ATC  ·  Page {self.page_no()}", align="C")
        self.set_text_color(*BLACK)


def fr(pdf, x, y, w, h, fill, border=BORDER, lw=0.25):
    pdf.set_fill_color(*fill)
    pdf.set_draw_color(*border)
    pdf.set_line_width(lw)
    pdf.rect(x, y, w, h, "FD")


def txt(pdf, x, y, text, size=9, bold=False, color=DARK_GREY, align="L", w=180):
    pdf.set_font(FONT, "B" if bold else "", size)
    pdf.set_text_color(*color)
    pdf.set_xy(x, y)
    pdf.cell(w, 5, text, align=align)


def mtxt(pdf, x, y, text, size=8, color=MID_GREY, w=80):
    pdf.set_font(FONT, "", size)
    pdf.set_text_color(*color)
    pdf.set_xy(x, y)
    pdf.multi_cell(w, 3.8, text)


def dot(pdf, x, y, text, size=7.5, color=DARK_GREY):
    pdf.set_font(FONT, "", size)
    pdf.set_text_color(*MID_GREY)
    pdf.set_xy(x, y)
    pdf.cell(4, 4, "·")
    pdf.set_text_color(*color)
    pdf.set_xy(x + 4, y)
    pdf.cell(120, 4, text)


def arrow_v(pdf, x, y1, y2, color=MID_GREY):
    pdf.set_draw_color(*color)
    pdf.set_line_width(0.5)
    pdf.line(x, y1, x, y2 - 2.5)
    pdf.set_fill_color(*color)
    pdf.polygon([(x, y2), (x - 1.5, y2 - 3), (x + 1.5, y2 - 3)], style="F")


def hdr_band(pdf, x, y, w, h, title, fill=MID_BLUE, size=9):
    fr(pdf, x, y, w, h, fill, border=fill, lw=0)
    pdf.set_font(FONT, "B", size)
    pdf.set_text_color(*WHITE)
    pdf.set_xy(x + 3, y + (h - 5) / 2)
    pdf.cell(w - 6, 5, title)
    pdf.set_text_color(*BLACK)


def phase_box(pdf, x, y, w, h, label, station, trigger_in, impl=True):
    fill = GREEN_LIGHT if impl else LIGHT_GREY
    border = GREEN_MID if impl else BORDER
    label_col = GREEN_DARK if impl else MID_GREY
    fr(pdf, x, y, w, h, fill, border=border, lw=0.5 if impl else 0.25)
    txt(pdf, x + 2, y + 1, label, size=8, bold=True, color=label_col, w=w - 4)
    txt(pdf, x + 2, y + 6, station, size=7, color=MID_BLUE if impl else MID_GREY, w=w - 4)
    if trigger_in:
        pdf.set_font(FONT, "", 6.5)
        pdf.set_text_color(*MID_GREY)
        pdf.set_xy(x + w + 2, y + 3)
        pdf.cell(55, 4, f"← {trigger_in}")


# ------------------------------------------------------------------ #
# Page 1 — System Architecture

def page_architecture(pdf: PDF):
    pdf.add_page()
    L, W = 14, 182

    # Title
    txt(pdf, L, 14, "X-Plane Virtual ATC", size=18, bold=True, color=DARK_BLUE, w=W, align="C")
    txt(pdf, L, 23, "System Architecture & Data Flow", size=11, color=MID_GREY, w=W, align="C")
    txt(pdf, L, 29, "June 2026", size=8, color=MID_GREY, w=W, align="C")

    y = 36

    # ── DATA SOURCES ──────────────────────────────────────────────
    fr(pdf, L, y, W, 48, LIGHT_BLUE, border=BORDER_BLUE, lw=0.4)
    hdr_band(pdf, L, y, W, 7, "DATA SOURCES", fill=MID_BLUE)

    BW = (W - 9) / 2

    # X-Plane box
    fr(pdf, L + 3, y + 9, BW, 35, (235, 244, 255), border=MID_BLUE, lw=0.4)
    txt(pdf, L + 5, y + 10, "X-Plane 12  (live flight)", size=8, bold=True, color=DARK_BLUE, w=BW - 4)
    dot(pdf, L + 5, y + 15, "UDP port 49000 — RREF protocol")
    dot(pdf, L + 5, y + 19, "26 DataRef subscriptions at 1–5 Hz")
    dot(pdf, L + 5, y + 23, "position · speed · attitude · heading")
    dot(pdf, L + 5, y + 27, "COM1/COM2 · squawk · aircraft type")
    dot(pdf, L + 5, y + 31, "on_ground · IAS · groundspeed")

    # Simulator box
    sx = L + 6 + BW
    fr(pdf, sx, y + 9, BW, 35, GREEN_LIGHT, border=GREEN_MID, lw=0.4)
    txt(pdf, sx + 2, y + 10, "ScenarioSimulator  (no X-Plane needed)", size=8, bold=True, color=GREEN_DARK, w=BW - 4)
    dot(pdf, sx + 2, y + 15, "scenarios/*.json — static FlightState")
    dot(pdf, sx + 2, y + 19, "airport ICAO · aircraft ICAO · callsign")
    dot(pdf, sx + 2, y + 23, "weather: QNH · wind dir/kts · ATIS")
    dot(pdf, sx + 2, y + 27, "same FlightDataSource Protocol as live")
    dot(pdf, sx + 2, y + 31, "switchable at runtime via WebSocket msg")

    # Protocol label centre
    txt(pdf, L, y + 45, "Both implement FlightDataSource Protocol  (.state → FlightState)", size=7, color=MID_GREY, w=W, align="C")

    y += 48
    arrow_v(pdf, L + W / 2, y, y + 9)
    y += 9

    # ── PYTHON BACKEND ────────────────────────────────────────────
    BH = 106
    fr(pdf, L, y, W, BH, (245, 248, 250), border=BORDER_BLUE, lw=0.4)
    hdr_band(pdf, L, y, W, 7, "PYTHON BACKEND  (backend/server.py)  ·  WebSocket ws://localhost:8765", fill=DARK_BLUE)

    IW = W - 6

    # AirportDB + AircraftDB side by side
    DBW = (IW - 3) / 2
    fr(pdf, L + 3, y + 9, DBW, 13, LIGHT_GREY, border=BORDER)
    txt(pdf, L + 5, y + 10, "AirportDB", size=8, bold=True, color=DARK_GREY, w=DBW - 4)
    txt(pdf, L + 5, y + 15, "31,290 airports · 1°×1° spatial grid · runway containment", size=7, color=MID_GREY, w=DBW - 4)

    fr(pdf, L + 6 + DBW, y + 9, DBW, 13, LIGHT_GREY, border=BORDER)
    txt(pdf, L + 8 + DBW, y + 10, "AircraftDB", size=8, bold=True, color=DARK_GREY, w=DBW - 4)
    txt(pdf, L + 8 + DBW, y + 15, "23 ICAO types · cruise speed · wake category · MTOW", size=7, color=MID_GREY, w=DBW - 4)

    # Arrow inside backend
    arrow_v(pdf, L + W / 2, y + 22, y + 30, MID_GREY)
    txt(pdf, L, y + 23, "nearest() → Airport  +  Opus boundary_check()", size=7, color=MID_GREY, w=W, align="C")

    # Opus boundary check
    fr(pdf, L + 3, y + 30, IW, 13, AMBER_LIGHT, border=(200, 120, 0), lw=0.4)
    txt(pdf, L + 5, y + 31, "Opus boundary_check()  [claude-opus-4-8]  — once per airport load", size=8, bold=True, color=AMBER_DARK, w=IW - 4)
    txt(pdf, L + 5, y + 36, "Returns: active_runway · atc_callsign · VFR operational notes", size=7, color=AMBER_DARK, w=IW - 4)

    arrow_v(pdf, L + W / 2, y + 43, y + 51, MID_GREY)

    # ATCSession
    fr(pdf, L + 3, y + 51, IW, 32, GREEN_LIGHT, border=GREEN_MID, lw=0.6)
    txt(pdf, L + 5, y + 52, "ATCSession  (atc/session.py)", size=8, bold=True, color=GREEN_DARK, w=IW - 4)
    dot(pdf, L + 5, y + 57, "Phase + Station state machine  (PRE_DEPARTURE → GROUND_DEPARTURE → DEPARTING → EN_ROUTE → APPROACH → CIRCUIT)", size=7)
    dot(pdf, L + 5, y + 61, "Squawk lifecycle: pre-assign before Tower · 7000 on CTR exit · reassign at Approach", size=7)
    dot(pdf, L + 5, y + 65, "Frequency handoff parsed from ATC response text → station switch", size=7)
    dot(pdf, L + 5, y + 69, "Pilot message parsed for station switch  (word-boundary safe — 'approaching' ≠ 'approach')", size=7)
    dot(pdf, L + 5, y + 73, "Model selection: Opus on first call per station  ·  Sonnet for all subsequent exchanges", size=7)

    arrow_v(pdf, L + W / 2, y + 83, y + 91, MID_GREY)

    # Engine
    fr(pdf, L + 3, y + 91, IW, 13, LIGHT_BLUE, border=BORDER_BLUE, lw=0.4)
    txt(pdf, L + 5, y + 92, "atc/engine.respond()  →  claude -p  (subprocess · 90 s timeout · asyncio.to_thread)", size=8, bold=True, color=DARK_BLUE, w=IW - 4)
    txt(pdf, L + 5, y + 97, "Prompt: situation (airport · aircraft · weather) + session history (6 ex.) + extra_instructions", size=7, color=MID_GREY, w=IW - 4)

    y += BH
    arrow_v(pdf, L + W / 2, y, y + 9)
    y += 9

    # ── TAURI UI ──────────────────────────────────────────────────
    fr(pdf, L, y, W, 57, (230, 236, 242), border=BORDER_BLUE, lw=0.4)
    hdr_band(pdf, L, y, W, 7, "TAURI UI  (ui/)  ·  Svelte 5 + SvelteKit + Vite + WebSocket auto-reconnect", fill=(52, 73, 120))

    # StatusBar strip
    fr(pdf, L + 3, y + 9, IW, 6, (215, 222, 230), border=BORDER)
    txt(pdf, L + 5, y + 10, "StatusBar  ·  ● backend uptime  ·  SIMULATED / X-PLANE badge  ·  ATC callsign  ·  Phase  ·  Station", size=7, color=DARK_GREY, w=IW - 4)

    # Three panels
    PW = (IW - 6) / 3
    panels = [
        ("AircraftPanel",  "(260 px)",  ["C172  D-EIYD", "ON GROUND", "ALT 183 ft  IAS 0 kt", "HDG 270°  GS 0 kt", "Position: 52.4608° N", "COM1 121.955 ●  XPDR 1200"]),
        ("RadioPane",      "(flex)",    ["● Hannover Ground", "", "ATC: D-EIYD, startup approved,", "  QNH 1018, taxi via Alpha…", "PLT: QNH 1018, taxi Alpha…", "[ Transmit… ]   [▶ TX]"]),
        ("AirportPanel",   "(260 px)",  ["EDDV  Hannover  183 ft", "09R/27L · 09C/27C · 09L/27R", "", "GND 121.955 ●   TWR 120.180", "TWR 120.405   ATIS 136.575", "APP 119.605   DEP 131.330"]),
    ]
    for i, (title, sub, lines) in enumerate(panels):
        px = L + 3 + i * (PW + 3)
        fr(pdf, px, y + 17, PW, 37, WHITE, border=BORDER)
        txt(pdf, px + 2, y + 18, title, size=7, bold=True, color=DARK_BLUE, w=PW - 4)
        txt(pdf, px + 2, y + 22, sub, size=6.5, color=MID_GREY, w=PW - 4)
        for j, line in enumerate(lines):
            pdf.set_font(FONT, "", 6.5)
            pdf.set_text_color(*DARK_GREY)
            pdf.set_xy(px + 2, y + 26 + j * 4.2)
            pdf.cell(PW - 4, 4, line)

    txt(pdf, L, y + 54, "+ ScenarioDrawer: preset picker · aircraft · airport · weather · on-ground/airborne toggle", size=7, color=MID_GREY, w=W, align="C")


# ------------------------------------------------------------------ #
# Page 2 — Session State Machine (full page)

def page_state_machine(pdf: PDF):
    pdf.add_page()
    L, W = 14, 182

    txt(pdf, L, 14, "Session State Machine", size=15, bold=True, color=DARK_BLUE, w=W, align="C")
    txt(pdf, L, 22, "Phase transitions triggered by LLM response content · pilot station switches · frequency handoffs", size=8, color=MID_GREY, w=W, align="C")

    y = 30

    PX = L + 12          # left edge of phase boxes
    PW = 84              # phase box width
    PH = 15              # phase box height (taller for breathing room)
    AG = 8               # arrow gap between boxes
    AX = PX + PW / 2    # arrow x centre

    phases = [
        # (label, station, trigger_in, impl)
        ("PRE_DEPARTURE",    "Station: GND",                                    None,                                          True),
        ("GROUND_DEPARTURE", "Station: GND  ·  Opus on first call",             "pilot calls Hannover Ground / scenario load",  True),
        ("TAXIING",          "Station: GND",                                    "ATC issues taxi instruction",                  False),
        ("DEPARTING",        "Station: TWR  ·  Squawk assigned  ·  Opus",       "pilot calls Tower / freq handoff detected",    True),
        ("EN_ROUTE",         "Station: RADAR  ·  Squawk 7000",                  '"cleared for takeoff" in ATC response',        True),
        ("EN_ROUTE_FIS",     "Station: FIS",                                    '"radar contact" in ATC response',              True),
        ("APPROACH",         "Station: APP  ·  New squawk assigned  ·  Opus",   "freq → FIS detected in ATC response",          True),
        ("CIRCUIT",          "Station: TWR at destination  ·  Opus",            "freq → APP detected in ATC response",          True),
        ("GROUND_ARRIVAL",   "Station: GND at destination",                     '"cleared to land" in ATC response',            False),
        ("PARKED",           "Station: GND",                                    "freq → GND detected in ATC response",          False),
    ]

    # Calculate total content height to size the outer box correctly
    n = len(phases)
    content_h = 14 + n * PH + (n - 1) * AG + 12   # top pad + phases + arrows + legend + bottom pad
    outer_h = content_h + 4

    fr(pdf, L, y, W, outer_h, (248, 249, 250), border=BORDER, lw=0.3)
    hdr_band(pdf, L, y, W, 7, "ATCSession  Phase State Machine  (atc/session.py)", fill=DARK_BLUE)

    py = y + 14   # first box starts with generous top padding

    for i, (label, station, trigger_in, impl) in enumerate(phases):
        if i > 0:
            arrow_v(pdf, AX, py, py + AG, GREEN_MID if impl else MID_GREY)
            py += AG

        phase_box(pdf, PX, py, PW, PH, label, station, trigger_in, impl)
        py += PH

    # Legend sits inside the outer box, below last phase
    ly = py + 5
    fr(pdf, L + 3, ly, 14, 5, GREEN_LIGHT, border=GREEN_MID, lw=0.4)
    txt(pdf, L + 19, ly, "Implemented & tested", size=7, color=GREEN_DARK, w=55)
    fr(pdf, L + 80, ly, 14, 5, LIGHT_GREY, border=BORDER)
    txt(pdf, L + 96, ly, "Not yet wired in backend", size=7, color=MID_GREY, w=65)


# ------------------------------------------------------------------ #
# Page 3 — Test Coverage

def page_test_coverage(pdf: PDF):
    pdf.add_page()
    L, W = 14, 182

    txt(pdf, L, 14, "Test Coverage", size=15, bold=True, color=DARK_BLUE, w=W, align="C")
    txt(pdf, L, 22, "All tests mocked — no running LLM or X-Plane instance required", size=8, color=MID_GREY, w=W, align="C")

    y = 30

    # ── TEST COVERAGE ─────────────────────────────────────────────
    fr(pdf, L, y, W, 100, (248, 249, 250), border=BORDER, lw=0.3)
    hdr_band(pdf, L, y, W, 7, "Test Coverage  (pytest  ·  all mocked — no LLM or X-Plane needed)", fill=(52, 73, 94))

    # Table header
    col_w = [58, 14, W - 58 - 14 - 6]
    cx = [L + 3, L + 3 + col_w[0], L + 3 + col_w[0] + col_w[1]]
    row_h = 6.5
    ty = y + 9

    fr(pdf, cx[0], ty, col_w[0], row_h, (52, 73, 94), border=(52, 73, 94))
    fr(pdf, cx[1], ty, col_w[1], row_h, (52, 73, 94), border=(52, 73, 94))
    fr(pdf, cx[2], ty, col_w[2], row_h, (52, 73, 94), border=(52, 73, 94))
    txt(pdf, cx[0] + 1, ty + 1, "Test file", size=7.5, bold=True, color=WHITE, w=col_w[0] - 2)
    txt(pdf, cx[1] + 1, ty + 1, "Tests", size=7.5, bold=True, color=WHITE, w=col_w[1] - 2, align="C")
    txt(pdf, cx[2] + 1, ty + 1, "What it covers", size=7.5, bold=True, color=WHITE, w=col_w[2] - 2)
    ty += row_h

    rows = [
        ("test_session.py",               "78",  "ATCSession: response parsing, squawk lifecycle, phase/station transitions, model selection, extra_instructions delivery, CTR exit guard, freq→station lookup"),
        ("test_atc_parser.py",            "17",  "Radio call parsing: station extraction, callsign formats (EU/US/airline), word-boundary fix ('approaching' ≠ 'approach')"),
        ("test_airport_parser.py",        "14",  "apt.dat parsing: freq codes 50–56 and 1050–1056 (X-Plane 12), pickle cache, 5-/6-digit frequency conversion"),
        ("test_airport_database.py",      "14",  "Haversine distance, nearest-airport spatial query, runway rectangle containment (on/off/margin)"),
        ("test_aircraft_database.py",     " 8",  "ICAO type lookup, performance data, case-insensitive matching, describe() output"),
        ("test_xplane_connector.py",      "21",  "RREF packet layout, FlightState unit conversions, DataRef dispatch, deepcopy isolation"),
        ("test_full_flight_exchange.py",  "58",  "Aspirational EDDV→EDDG full-flight spec: 38 xpass (implemented), 20 xfail (en-route / arrival phases pending)"),
    ]

    for r, (fname, count, desc) in enumerate(rows):
        bg = WHITE if r % 2 == 0 else (248, 250, 252)
        fr(pdf, cx[0], ty, col_w[0], row_h, bg, border=BORDER, lw=0.2)
        fr(pdf, cx[1], ty, col_w[1], row_h, bg, border=BORDER, lw=0.2)
        fr(pdf, cx[2], ty, col_w[2], row_h, bg, border=BORDER, lw=0.2)
        txt(pdf, cx[0] + 1, ty + 1, fname, size=7, color=DARK_BLUE, w=col_w[0] - 2)
        txt(pdf, cx[1] + 1, ty + 1, count, size=7, bold=True, color=DARK_GREY, w=col_w[1] - 2, align="C")
        # Desc may wrap — use multi_cell approach with reduced size
        pdf.set_font(FONT, "", 6.8)
        pdf.set_text_color(*DARK_GREY)
        pdf.set_xy(cx[2] + 1, ty + 0.5)
        pdf.multi_cell(col_w[2] - 2, 3.2, desc)
        ty += row_h

    # Totals row
    fr(pdf, cx[0], ty, col_w[0], row_h, LIGHT_BLUE, border=BORDER_BLUE, lw=0.3)
    fr(pdf, cx[1], ty, col_w[1], row_h, LIGHT_BLUE, border=BORDER_BLUE, lw=0.3)
    fr(pdf, cx[2], ty, col_w[2], row_h, LIGHT_BLUE, border=BORDER_BLUE, lw=0.3)
    txt(pdf, cx[0] + 1, ty + 1, "Total", size=7.5, bold=True, color=DARK_BLUE, w=col_w[0] - 2)
    txt(pdf, cx[1] + 1, ty + 1, "210", size=7.5, bold=True, color=DARK_BLUE, w=col_w[1] - 2, align="C")
    txt(pdf, cx[2] + 1, ty + 1, "139 pass · 38 xpass · 20 xfail  (xpass = aspirational tests now working)", size=7.5, bold=True, color=DARK_BLUE, w=col_w[2] - 2)


# ------------------------------------------------------------------ #

def main():
    out = Path(__file__).parent / "architecture.pdf"

    pdf = PDF("P", "mm", "A4")
    pdf.set_auto_page_break(auto=True, margin=14)

    page_architecture(pdf)
    page_state_machine(pdf)
    page_test_coverage(pdf)

    pdf.output(str(out))
    print(f"Written: {out}")


if __name__ == "__main__":
    main()
