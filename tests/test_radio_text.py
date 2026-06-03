"""Tests for audio.radio_text.to_spoken — the pre-TTS radio normalizer."""

from audio.radio_text import to_spoken


class TestCallsigns:
    def test_registration_to_nato(self):
        assert to_spoken("D-EIYD") == "Delta Echo India Yankee Delta"

    def test_registration_in_sentence(self):
        assert to_spoken("D-EIYD, readback correct.").startswith(
            "Delta Echo India Yankee Delta,")

    def test_us_registration_with_digits(self):
        assert to_spoken("N-12A") == "November one two Alpha"

    def test_plain_words_untouched(self):
        # No hyphen → not a callsign; German words must survive
        assert to_spoken("Hannover Ground, guten Tag") == "Hannover Ground, guten Tag"


class TestNumbers:
    def test_frequency(self):
        assert to_spoken("contact Tower 120.180") == \
            "contact Tower one two zero decimal one eight zero"

    def test_runway_with_side(self):
        assert to_spoken("runway 27R") == "runway two seven Right"

    def test_runway_no_side(self):
        assert to_spoken("runway 9") == "runway niner"

    def test_squawk(self):
        assert to_spoken("squawk 3674") == "squawk three six seven four"

    def test_qnh(self):
        assert to_spoken("QNH 1018") == "QNH one zero one eight"

    def test_wind(self):
        assert to_spoken("wind 270 degrees 8 knots") == \
            "wind two seven zero degrees eight knots"


class TestTaxiways:
    def test_single_taxiway_letter(self):
        assert to_spoken("taxiway L") == "taxiway Lima"

    def test_taxiways_list(self):
        assert to_spoken("via taxiways L and B") == "via taxiways Lima and Bravo"

    def test_holding_point_with_number(self):
        assert to_spoken("holding point B1") == "holding point Bravo one"


class TestFullLine:
    def test_realistic_clearance(self):
        out = to_spoken(
            "D-EIYD, Hannover Ground, guten Tag, runway 27R, QNH 1018, "
            "taxi via taxiways L and B to holding point B1, squawk 3674."
        )
        assert "Delta Echo India Yankee Delta" in out
        assert "runway two seven Right" in out
        assert "QNH one zero one eight" in out
        assert "Lima and Bravo" in out
        assert "holding point Bravo one" in out
        assert "squawk three six seven four" in out
        assert "Hannover Ground" in out and "guten Tag" in out   # untouched
