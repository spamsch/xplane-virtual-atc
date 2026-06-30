"""Pure-logic tests for the historical LiveATC layer: feed parsing, archive-URL
construction, 30-minute block alignment, and silence-gated clip segmentation.
The network + ffmpeg paths are best-effort and not exercised here."""

from datetime import datetime, timezone

import numpy as np
import pytest

from traffic import liveatc as la


def test_parse_feeds_dedups_and_orders():
    html = (
        '<a href="/play/eddf_twr.pls">Frankfurt Tower</a>'
        '<a href="/play/eddf_gnd.pls">Ground</a>'
        '<a href="hlisten.php?mount=eddf_app&icao=EDDF">Approach</a>'
        '<a href="/play/eddf_twr.pls">duplicate</a>'
    )
    feeds = la.parse_feeds(html, "EDDF")
    assert [f.mount for f in feeds] == ["eddf_twr", "eddf_gnd", "eddf_app"]


def test_parse_feeds_empty_when_no_coverage():
    assert la.parse_feeds("<html>no feeds here</html>", "EDLI") == []
    assert la.parse_feeds("", "EDLI") == []


def test_block_align_floors_to_half_hour():
    assert la.block_align(datetime(2024, 1, 15, 20, 17, tzinfo=timezone.utc)).minute == 0
    assert la.block_align(datetime(2024, 1, 15, 20, 47, tzinfo=timezone.utc)).minute == 30
    aligned = la.block_align(datetime(2024, 1, 15, 20, 47, 33, tzinfo=timezone.utc))
    assert aligned.second == 0 and aligned.microsecond == 0


def test_recent_blocks_are_past_and_descending():
    now = datetime(2024, 1, 15, 20, 17, tzinfo=timezone.utc)
    blocks = la.recent_blocks(now, 3)
    assert [b.strftime("%H%M") for b in blocks] == ["1930", "1900", "1830"]
    # All strictly before the in-progress block.
    assert all(b < la.block_align(now) for b in blocks)


def test_archive_url_format():
    dt = datetime(2024, 1, 15, 20, 0, tzinfo=timezone.utc)
    url = la.archive_url("eddf_twr", dt)
    assert url == "https://archive.liveatc.net/eddf_twr/eddf_twr-Jan-15-2024-2000Z.mp3"


def _voiced(sr, start_s, dur_s, n):
    t = np.arange(start_s * sr, (start_s + dur_s) * sr) / sr
    sig = np.zeros(n, dtype=np.float32)
    sig[int(start_s * sr):int((start_s + dur_s) * sr)] = (
        0.5 * np.sin(2 * np.pi * 300 * t)).astype(np.float32)
    return sig


def test_segment_splits_on_silence():
    sr = 16_000
    n = sr * 20
    sig = _voiced(sr, 1, 5, n) + _voiced(sr, 11, 5, n)
    clips = la.segment(sig, sr)
    assert len(clips) == 2
    for c in clips:
        assert 3.0 <= c.size / sr <= 11.0


def test_segment_empty_input():
    assert la.segment(np.array([], dtype=np.float32), 16_000) == []


def test_segment_caps_long_spans():
    sr = 16_000
    # One 30 s continuous tone — must be clamped to max_s.
    sig = _voiced(sr, 0, 30, sr * 31)
    clips = la.segment(sig, sr, max_s=11.0)
    assert clips and all(c.size / sr <= 11.0 + 0.5 for c in clips)
