/**
 * PTT mic-click sounds, synthesized with the Web Audio API (no audio assets).
 *
 * Real VHF radios give you a mechanical relay "clack" when you key the mic and
 * a short squelch-tail hiss when you unkey. We fake both with a filtered
 * noise burst plus, on key, a fast low-frequency thump for the relay body.
 *
 * The AudioContext is created lazily on first use — PTT is always triggered by
 * a user gesture (button/space) or after the user has interacted with the app,
 * so the browser autoplay policy lets it resume.
 */

let _ctx = null;

function _audioCtx() {
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return null;
  if (!_ctx) _ctx = new AC();
  if (_ctx.state === 'suspended') _ctx.resume();
  return _ctx;
}

/** A buffer of white noise `durationSec` long. */
function _noise(ctx, durationSec) {
  const n = Math.max(1, Math.floor(ctx.sampleRate * durationSec));
  const buf = ctx.createBuffer(1, n, ctx.sampleRate);
  const d = buf.getChannelData(0);
  for (let i = 0; i < n; i++) d[i] = Math.random() * 2 - 1;
  return buf;
}

/**
 * Play a mic click.
 * @param {'key'|'unkey'} kind  'key' = press (clack), 'unkey' = release (squelch tail)
 */
export function playMicClick(kind = 'key') {
  const ctx = _audioCtx();
  if (!ctx) return;
  const t0 = ctx.currentTime;
  const out = ctx.destination;

  const unkey = kind === 'unkey';
  const dur   = unkey ? 0.10 : 0.055;   // release tail rings a touch longer
  const peak  = unkey ? 0.16 : 0.22;

  // ── Filtered noise burst — the "tsh"/"click" of the relay + squelch ──
  const src = ctx.createBufferSource();
  src.buffer = _noise(ctx, dur);

  const bp = ctx.createBiquadFilter();
  bp.type = 'bandpass';
  bp.frequency.value = unkey ? 1900 : 1300;
  bp.Q.value = 0.7;

  const hp = ctx.createBiquadFilter();
  hp.type = 'highpass';
  hp.frequency.value = 550;

  const g = ctx.createGain();
  g.gain.setValueAtTime(0.0001, t0);
  g.gain.linearRampToValueAtTime(peak, t0 + 0.002);          // ~2 ms attack
  g.gain.exponentialRampToValueAtTime(0.0008, t0 + dur);     // fast decay

  src.connect(bp); bp.connect(hp); hp.connect(g); g.connect(out);
  src.start(t0); src.stop(t0 + dur);

  // ── Key only: a short low thump so it reads as a mechanical "clack" ──
  if (!unkey) {
    const osc = ctx.createOscillator();
    osc.type = 'triangle';
    osc.frequency.setValueAtTime(230, t0);
    osc.frequency.exponentialRampToValueAtTime(95, t0 + 0.035);

    const og = ctx.createGain();
    og.gain.setValueAtTime(0.0001, t0);
    og.gain.linearRampToValueAtTime(0.18, t0 + 0.001);
    og.gain.exponentialRampToValueAtTime(0.0008, t0 + 0.045);

    osc.connect(og); og.connect(out);
    osc.start(t0); osc.stop(t0 + 0.05);
  }
}
