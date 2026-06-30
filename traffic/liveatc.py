"""
Historical LiveATC traffic, injected as background texture under the synthetic
party line.

This is the "something wild" layer: real recorded controller/pilot chatter for
the airport you're at, sliced into short clips and dropped into the quiet between
the synthetic exchanges — atmosphere, not integrated traffic. It does NOT match
your scenario (it's whatever happened on the feed that day: other runway, other
weather, other callsigns), and the synthetic world stays the foreground "traffic
you can follow".

Reality check, baked into the design:
  - LiveATC has no public API. We scrape the per-ICAO feed list and construct
    archive URLs by the conventional 30-minute-block naming. Both can break.
  - Archive access is gated behind a login; without a session cookie most
    downloads 403. Set LIVEATC_COOKIE to your logged-in session to actually pull
    audio. This is the user's own credential and their own listening — we don't
    redistribute anything.
  - Coverage is thin outside the US. Many fields (most German GA) have no feed,
    and the loader degrades cleanly to "none" — the synthetic world carries on.
  - Automated downloading is against LiveATC's ToS; this is opt-in (off by
    default) and intended for personal, local debugging only.

Everything network-touching is best-effort and wrapped; the pure pieces (feed
parsing, archive-URL construction, clip segmentation) are unit-tested.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

_CACHE_DIR = Path.home() / ".cache" / "xplane-vatc" / "liveatc"

# Conventional archive layout. The directory is the feed mount; the file is named
# <mount>-<Mon>-DD-YYYY-HHMM>Z.mp3 in 30-minute UTC blocks. Feeds whose archive
# prefix differs from the mount won't resolve — override the base if needed.
ARCHIVE_BASE = "https://archive.liveatc.net"
SEARCH_BASE = "https://www.liveatc.net/search/"

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
_UA += "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"


# ─────────────────────────────── model ───────────────────────────────────────

@dataclass(frozen=True)
class Feed:
    """One LiveATC feed for an airport (a mount = one receiver/frequency)."""
    mount: str
    label: str = ""


@dataclass
class LiveATCFeed:
    """A loaded historical feed for one airport: the clips we pulled and a status
    the UI can show. `clips` are float32 mono @ `sr`, already segmented."""
    icao: str
    status: str = "off"            # off | searching | ready | none | error
    message: str = ""
    feeds: list = field(default_factory=list)     # discovered Feed list
    clips: list = field(default_factory=list)     # list[np.ndarray]
    sr: int = 16_000

    def random_clip(self, rng) -> Optional[np.ndarray]:
        return rng.choice(self.clips) if self.clips else None


# ─────────────────────── pure: parsing + URL building ─────────────────────────

# Feed mounts appear in /play/<mount>.pls links and hlisten.php?mount=<mount>.
_MOUNT_RE = re.compile(r"(?:/play/|mount=)([A-Za-z0-9_\-]+?)(?:\.pls|[\"&'])")


def parse_feeds(html: str, icao: str) -> list[Feed]:
    """Extract feed mounts from a LiveATC search-result page. Order-preserving,
    de-duplicated. Empty when the page lists no feeds for the airport."""
    seen: dict[str, Feed] = {}
    for m in _MOUNT_RE.finditer(html or ""):
        mount = m.group(1).strip().lower()
        if not mount or mount in seen:
            continue
        # Skip obvious non-feed assets.
        if mount in ("search", "index", "play"):
            continue
        seen[mount] = Feed(mount=mount, label=mount)
    return list(seen.values())


def block_align(dt: datetime) -> datetime:
    """Floor a UTC time to its 30-minute archive block (:00 or :30)."""
    dt = dt.astimezone(timezone.utc)
    minute = 0 if dt.minute < 30 else 30
    return dt.replace(minute=minute, second=0, microsecond=0)


def recent_blocks(now: datetime, n: int) -> list[datetime]:
    """The `n` most recent *completed* 30-minute blocks before `now`, newest
    first. The block containing `now` is in progress, so we start one before it."""
    start = block_align(now) - timedelta(minutes=30)
    return [start - timedelta(minutes=30 * i) for i in range(max(0, n))]


def archive_url(mount: str, dt: datetime, *, base: str = ARCHIVE_BASE) -> str:
    """The archive MP3 URL for a feed mount and a 30-minute block start (UTC)."""
    dt = dt.astimezone(timezone.utc)
    stamp = dt.strftime("%b-%d-%Y-%H%M")   # e.g. Jan-15-2024-2000
    return f"{base.rstrip('/')}/{mount}/{mount}-{stamp}Z.mp3"


# ─────────────────────── pure: clip segmentation ──────────────────────────────

def segment(samples: np.ndarray, sr: int, *,
            min_s: float = 3.0, max_s: float = 8.0,
            silence_db: float = -38.0, pad_s: float = 0.15,
            max_clips: int = 40) -> list[np.ndarray]:
    """Split a long recording into short voiced clips on silence boundaries, so
    we play coherent bursts of chatter rather than mid-word cuts.

    Energy-gated: frames quieter than `silence_db` (relative to the clip's peak)
    are treated as gaps. Voiced spans are padded a touch and clamped to
    [min_s, max_s]. Returns at most `max_clips` clips."""
    if samples.size == 0:
        return []
    x = samples.astype(np.float32)
    peak = float(np.max(np.abs(x))) or 1.0
    frame = max(1, int(sr * 0.03))           # 30 ms frames
    n_frames = x.size // frame
    if n_frames == 0:
        return []
    framed = x[: n_frames * frame].reshape(n_frames, frame)
    rms = np.sqrt(np.mean(framed ** 2, axis=1) + 1e-9)
    thresh = peak * (10.0 ** (silence_db / 20.0))
    voiced = rms > thresh

    clips: list[np.ndarray] = []
    pad = int(sr * pad_s)
    min_n, max_n = int(sr * min_s), int(sr * max_s)
    i = 0
    while i < n_frames and len(clips) < max_clips:
        if not voiced[i]:
            i += 1
            continue
        j = i
        # Extend across short gaps (< ~0.4 s) so a clip isn't cut at every breath.
        gap = 0
        while j < n_frames and (voiced[j] or gap < int(0.4 / 0.03)):
            gap = 0 if voiced[j] else gap + 1
            j += 1
        start = max(0, i * frame - pad)
        end = min(x.size, j * frame + pad)
        clip = x[start:end]
        if clip.size >= min_n:
            # Hard-cap overly long spans.
            clips.append(clip[:max_n] if clip.size > max_n else clip)
        i = j + 1
    return clips


# ─────────────────────── network: best-effort fetch ───────────────────────────

def _http_get(url: str, *, cookie: str = "", timeout: float = 15.0) -> Optional[bytes]:
    from urllib.request import Request, urlopen
    headers = {"User-Agent": _UA, "Referer": "https://www.liveatc.net/"}
    if cookie:
        headers["Cookie"] = cookie
    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception as e:
        log.debug(f"LiveATC GET failed ({url}): {e}")
        return None


def resolve_feeds(icao: str, *, search_base: str = SEARCH_BASE,
                  cookie: str = "") -> list[Feed]:
    """Scrape the feed list for an ICAO. Empty on any failure / no coverage."""
    html = _http_get(f"{search_base}?icao={icao.upper()}", cookie=cookie)
    if html is None:
        return []
    try:
        return parse_feeds(html.decode("utf-8", "replace"), icao)
    except Exception as e:
        log.debug(f"LiveATC feed parse failed for {icao}: {e}")
        return []


def _decode_mp3(data: bytes, *, target_sr: int) -> Optional[np.ndarray]:
    """Decode MP3 bytes to mono float32 @ target_sr via ffmpeg. None if ffmpeg is
    missing or the decode fails."""
    if shutil.which("ffmpeg") is None:
        log.warning("LiveATC: ffmpeg not found — cannot decode archive audio")
        return None
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error",
             "-i", "pipe:0", "-ac", "1", "-ar", str(target_sr),
             "-f", "f32le", "pipe:1"],
            input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=60, check=True)
        return np.frombuffer(proc.stdout, dtype=np.float32).copy()
    except Exception as e:
        log.debug(f"LiveATC ffmpeg decode failed: {e}")
        return None


def fetch_clips(icao: str, *, cookie: str = "", lookback_blocks: int = 6,
                target_sr: int = 16_000, now: Optional[datetime] = None,
                search_base: str = SEARCH_BASE, archive_base: str = ARCHIVE_BASE,
                cache_dir: Path = _CACHE_DIR) -> LiveATCFeed:
    """Resolve a feed for the airport, download a recent archive block, decode and
    segment it. Returns a LiveATCFeed whose `status` says what happened — this is
    best-effort and frequently 'none' for fields LiveATC doesn't cover."""
    out = LiveATCFeed(icao=icao.upper(), status="searching", sr=target_sr)
    feeds = resolve_feeds(icao, search_base=search_base, cookie=cookie)
    out.feeds = feeds
    if not feeds:
        out.status = "none"
        out.message = "No LiveATC feed found for this airport."
        return out

    blocks = recent_blocks(now or datetime.now(timezone.utc), lookback_blocks)
    cache_dir.mkdir(parents=True, exist_ok=True)
    for feed in feeds:
        for dt in blocks:
            url = archive_url(feed.mount, dt, base=archive_base)
            cached = cache_dir / f"{feed.mount}-{dt:%Y%m%d-%H%M}.mp3"
            data = cached.read_bytes() if cached.exists() else _http_get(url, cookie=cookie)
            if not data or len(data) < 4096:
                continue
            if not cached.exists():
                try:
                    cached.write_bytes(data)
                except OSError:
                    pass
            samples = _decode_mp3(data, target_sr=target_sr)
            if samples is None or samples.size == 0:
                continue
            clips = segment(samples, target_sr)
            if clips:
                out.clips = clips
                out.sr = target_sr
                out.status = "ready"
                out.message = (f"{len(clips)} clips from {feed.mount} "
                               f"({dt:%b %d %H:%MZ}).")
                log.info(f"LiveATC: {out.message}")
                return out
    out.status = "none"
    out.message = ("Feed found but no archive block was reachable "
                   "(set LIVEATC_COOKIE to your logged-in session).")
    return out
