/**
 * WebSocket client with automatic reconnect.
 * Dispatches incoming messages to the Svelte stores.
 */

import {
  wsStatus, backendUptime, source, scenarioName, xplaneConnected,
  flightState, airport, activeRunway, atcCallsign, boundaryNotes,
  messages, phase, station, thinking, loading, loadingLabel,
  configStatus, pttActive, transcription, audioEnabled, vfrWeather,
  ambientLevel, flightplan, flightplanStage, flightplanFreq, liveatcStatus,
} from './store.js';
import { playMicClick } from './sound.js';

// ── Audio state (kept here so PTT logic is co-located with WS send) ───────
let _mediaRecorder = null;
let _audioChunks   = [];
let _micStream     = null;

const MAX_PTT_MS = 30_000;   // hard cap: 30 s max recording
let   _pttTimer  = null;

export async function requestMicPermission() {
  try {
    _micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    return true;
  } catch {
    return false;
  }
}

export async function startPTT() {
  if (_mediaRecorder?.state === 'recording') return;

  playMicClick('key');   // relay clack the instant the mic is keyed

  if (!_micStream) {
    _micStream = await navigator.mediaDevices.getUserMedia({ audio: true }).catch(() => null);
    if (!_micStream) return;
  }

  // Keying the mic silences the party line instantly — a radio is half-duplex,
  // and we also tell the backend to stop scheduling traffic.
  stopAmbientAudio();
  sendMessage('mic_open');

  _audioChunks   = [];
  _mediaRecorder = new MediaRecorder(_micStream);
  _mediaRecorder.ondataavailable = e => { if (e.data.size > 0) _audioChunks.push(e.data); };
  _mediaRecorder.start();
  pttActive.set(true);
  transcription.set('');

  // Safety: auto-release after MAX_PTT_MS so PTT can't get stuck
  _pttTimer = setTimeout(stopPTT, MAX_PTT_MS);
}

export async function stopPTT() {
  if (!_mediaRecorder || _mediaRecorder.state !== 'recording') return;
  if (_pttTimer) { clearTimeout(_pttTimer); _pttTimer = null; }

  playMicClick('unkey');   // squelch-tail hiss when the mic is unkeyed
  sendMessage('mic_close');

  await new Promise(resolve => {
    _mediaRecorder.onstop = resolve;
    _mediaRecorder.stop();
  });

  pttActive.set(false);
  if (_audioChunks.length === 0) return;

  // MediaRecorder default is audio/webm (Chromium/Tauri) — transcode to
  // 16 kHz mono 16-bit PCM WAV so the backend gets standard audio.
  const rawBlob = new Blob(_audioChunks, { type: _mediaRecorder.mimeType });
  try {
    const wavBytes = await _transcodeToWav(rawBlob);
    sendMessage('pilot_audio', { audio: _toBase64(wavBytes) });
  } catch (e) {
    console.error('[audio] transcode failed:', e);
  }
}

/** Decode any browser-recorded blob → resample to 16 kHz mono → PCM WAV bytes. */
async function _transcodeToWav(blob) {
  const STT_SR = 16_000;
  const rawBuf = await blob.arrayBuffer();
  const audioCtx = new AudioContext();
  const decoded  = await audioCtx.decodeAudioData(rawBuf);
  audioCtx.close();

  // Resample to STT_SR using OfflineAudioContext
  const frames = Math.ceil(decoded.duration * STT_SR);
  const offline = new OfflineAudioContext(1, frames, STT_SR);
  const src     = offline.createBufferSource();
  src.buffer    = decoded;
  src.connect(offline.destination);
  src.start();
  const rendered = await offline.startRendering();

  // Float32 → Int16
  const pcm   = rendered.getChannelData(0);
  const int16 = new Int16Array(pcm.length);
  for (let i = 0; i < pcm.length; i++) {
    const s = Math.max(-1, Math.min(1, pcm[i]));
    int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
  }

  // Build RIFF/WAVE header
  const wav  = new ArrayBuffer(44 + int16.byteLength);
  const view = new DataView(wav);
  const str  = (off, s) => { for (let i = 0; i < s.length; i++) view.setUint8(off + i, s.charCodeAt(i)); };
  str(0, 'RIFF');
  view.setUint32(4,  36 + int16.byteLength, true);
  str(8, 'WAVE');
  str(12, 'fmt ');
  view.setUint32(16, 16, true);        // chunk size
  view.setUint16(20, 1,  true);        // PCM
  view.setUint16(22, 1,  true);        // mono
  view.setUint32(24, STT_SR, true);    // sample rate
  view.setUint32(28, STT_SR * 2, true);// byte rate
  view.setUint16(32, 2,  true);        // block align
  view.setUint16(34, 16, true);        // bits per sample
  str(36, 'data');
  view.setUint32(40, int16.byteLength, true);
  new Int16Array(wav, 44).set(int16);

  return new Uint8Array(wav);
}

/** base64-encode a Uint8Array in chunks to avoid arg-count stack overflow. */
function _toBase64(bytes) {
  let str = '';
  const CHUNK = 8192;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    str += String.fromCharCode(...bytes.subarray(i, Math.min(i + CHUNK, bytes.length)));
  }
  return btoa(str);
}

function _b64ToBlobUrl(b64) {
  const binary = atob(b64);
  const bytes  = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return URL.createObjectURL(new Blob([bytes], { type: 'audio/wav' }));
}

function _playAtcAudio(b64) {
  const url = _b64ToBlobUrl(b64);
  const audio = new Audio(url);
  audio.onended = () => URL.revokeObjectURL(url);
  audio.play().catch(e => console.warn('[audio] play failed:', e));
}

// Ambient ("party line") playback — tracked so we can cut it the instant the
// pilot keys the mic or the backend says the frequency is now ours.
let _ambientAudio = null;
let _ambientUrl   = null;

function _playAmbientAudio(b64) {
  stopAmbientAudio();
  _ambientUrl = _b64ToBlobUrl(b64);
  _ambientAudio = new Audio(_ambientUrl);
  _ambientAudio.onended = () => stopAmbientAudio();
  _ambientAudio.play().catch(e => console.warn('[ambient] play failed:', e));
}

export function stopAmbientAudio() {
  if (_ambientAudio) {
    try { _ambientAudio.pause(); } catch {}
    _ambientAudio = null;
  }
  if (_ambientUrl) { URL.revokeObjectURL(_ambientUrl); _ambientUrl = null; }
}

const WS_URL = 'ws://localhost:8765';
const RECONNECT_DELAY_MS = 3000;

let ws = null;
let reconnectTimer = null;

function dispatch(msg) {
  switch (msg.type) {
    case 'backend_status':
      backendUptime.set(msg.uptime_s);
      source.set(msg.source);
      if (msg.xplane_connected !== undefined) xplaneConnected.set(msg.xplane_connected);
      // Gate mic UI on backend capability, not just local mic permission
      if (!msg.audio_ready) audioEnabled.set(false);
      break;

    case 'xplane_status':
      xplaneConnected.set(msg.connected);
      break;

    case 'loading':
      loading.set(msg.active);
      loadingLabel.set(msg.label ?? '');
      break;

    case 'vfr_weather':
      vfrWeather.set({ busy: !!msg.busy, ok: msg.ok ?? null, message: msg.message ?? '' });
      break;

    case 'config_status':
      configStatus.set(msg);
      if (msg.current?.ambient_level) ambientLevel.set(msg.current.ambient_level);
      break;

    case 'liveatc_status':
      liveatcStatus.set(msg);
      break;

    case 'state_update':
      flightState.set(msg);
      break;

    case 'airport_detected':
      airport.set(msg);
      break;

    case 'phase_change':
      phase.set(msg.phase);
      station.set(msg.station);
      if (msg.atc_callsign) atcCallsign.set(msg.atc_callsign);
      if (msg.active_runway) activeRunway.set(msg.active_runway);
      if (msg.notes) boundaryNotes.set(msg.notes);
      break;

    case 'flightplan_loaded': {
      const { type, ...fp } = msg;
      flightplan.set(fp);
      if (fp.stage) flightplanStage.set(fp.stage);
      break;
    }

    case 'flightplan_stage':
      flightplanStage.set(msg.stage);
      if (msg.station) station.set(msg.station);
      if (msg.atc_callsign) atcCallsign.set(msg.atc_callsign);
      flightplanFreq.set(msg.expected_freq ?? null);
      break;

    case 'flightplan_cleared':
      flightplan.set(null);
      flightplanStage.set(null);
      flightplanFreq.set(null);
      break;

    case 'atc_message':
      messages.update(list => [...list, {
        role: msg.role,
        text: msg.text,
        model: msg.model,
        timestamp: msg.timestamp ?? Date.now() / 1000,
      }]);
      break;

    case 'thinking':
      thinking.set(msg.thinking);
      break;

    case 'source_change':
      source.set(msg.source);
      scenarioName.set(msg.scenario_name ?? null);
      break;

    case 'atc_audio':
      _playAtcAudio(msg.audio);
      break;

    case 'ambient_audio':
      _playAmbientAudio(msg.audio);
      messages.update(list => [...list, {
        role: 'ambient',
        speaker: msg.speaker,         // 'pilot' | 'atc'
        callsign: msg.callsign,
        text: msg.text,
        timestamp: Date.now() / 1000,
      }]);
      break;

    case 'ambient_noise':
      // Background radio atmosphere — play it, no chat bubble.
      _playAmbientAudio(msg.audio);
      break;

    case 'ambient_stop':
      stopAmbientAudio();
      break;

    case 'transcription':
      transcription.set(msg.text);
      break;

    case 'ptt_start':
      startPTT();
      break;

    case 'ptt_end':
      stopPTT();
      break;

    case 'flight_reset':
      stopPTT();
      stopAmbientAudio();
      messages.set([]);
      transcription.set('');
      pttActive.set(false);
      phase.set('pre_departure');
      station.set('ground');
      atcCallsign.set('Ground');
      activeRunway.set(null);
      boundaryNotes.set('');
      airport.set(null);
      break;

    case 'error':
      console.error('[backend]', msg.message);
      messages.update(list => [...list, {
        role: 'system',
        text: `[!] ${msg.message}`,
        model: null,
        timestamp: Date.now() / 1000,
      }]);
      break;
  }
}

export function sendMessage(type, data = {}) {
  if (ws?.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type, ...data }));
  }
}

export function setVfrWeather() {
  vfrWeather.set({ busy: true, ok: null, message: 'Setting VFR day…' });
  sendMessage('set_vfr_weather');
}

export function sendTransmission(text) {
  sendMessage('pilot_transmission', { text });
}

export function loadScenario(scenarioObj) {
  sendMessage('load_scenario', { scenario: scenarioObj });
}

/** Upload an ICAO route ("EDLI OSN EDDG") to stage the journey. `overrides` is
 *  an optional {ICAO: bool} map forcing a field's controlled status. */
export function loadFlightplan(route, overrides = null, callsign = null) {
  const payload = { route };
  if (overrides) payload.controlled_overrides = overrides;
  if (callsign)  payload.callsign = callsign;
  sendMessage('load_flightplan', payload);
}

export function clearFlightplan() {
  sendMessage('clear_flightplan');
}

/** Tune COM1 in X-Plane to a frequency (MHz). */
export function tuneCom1(freqMhz) {
  sendMessage('tune_com1', { freq_mhz: freqMhz });
}

/** Simulated mode: teleport the aircraft. `pos` is any subset of
 *  {lat, lon, alt_ft, heading, on_ground, gs_kts, ias_kts}. */
export function moveAircraft(pos) {
  sendMessage('move_aircraft', pos);
}

/** Simulated mode: jump to a named stage of the journey —
 *  'stand'|'departure'|'enroute'|'arrival'|'final'|'landed'. */
export function routeJump(to) {
  sendMessage('route_jump', { to });
}

export function setSource(src) {
  sendMessage('set_source', { source: src });
}

export function newFlight() {
  sendMessage('new_flight');
}

export function setConfig(cfg) {
  sendMessage('set_config', { config: cfg });
}

/** Audition the configured controller voice (plays a short sample). */
export function previewVoice() {
  sendMessage('preview_voice');
}

/** Set ambient-traffic density: 'off' | 'light' | 'medium' | 'heavy'. */
export function setAmbient(level) {
  ambientLevel.set(level);          // optimistic; backend echoes via config_status
  sendMessage('set_ambient', { level });
}

function connect() {
  wsStatus.set('connecting');
  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    wsStatus.set('connected');
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    console.log('[ws] connected');
  };

  ws.onmessage = (event) => {
    try {
      dispatch(JSON.parse(event.data));
    } catch (e) {
      console.warn('[ws] parse error', e);
    }
  };

  ws.onerror = () => {
    wsStatus.set('error');
  };

  ws.onclose = () => {
    wsStatus.set('disconnected');
    ws = null;
    console.log(`[ws] disconnected — retrying in ${RECONNECT_DELAY_MS}ms`);
    reconnectTimer = setTimeout(connect, RECONNECT_DELAY_MS);
  };
}

export function initWs() {
  if (!ws) connect();
}

export function closeWs() {
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
  ws?.close();
  ws = null;
}
