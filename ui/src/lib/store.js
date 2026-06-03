import { writable, derived } from 'svelte/store';

// Connection
export const wsStatus = writable('disconnected'); // 'disconnected'|'connecting'|'connected'|'error'
export const backendUptime = writable(0);
export const source = writable('simulated');       // 'xplane'|'simulated'
export const xplaneConnected = writable(false);    // X-Plane REST API reachable right now
export const scenarioName = writable(null);

// Flight state (mirrors FlightState in Python)
export const flightState = writable(null);

// Airport
export const airport = writable(null);
export const activeRunway = writable(null);
export const atcCallsign = writable('Ground');
export const boundaryNotes = writable('');

// Session
export const messages = writable([]);   // [{role, text, model, timestamp}]
export const phase = writable('PRE_DEPARTURE');
export const station = writable('GND');
export const thinking = writable(false);

// UI
export const scenarioDrawerOpen = writable(false);
export const loading      = writable(false);   // backend is setting up a session (boundary check)
export const loadingLabel = writable('');       // human-readable loading message
export const configStatus = writable(null);     // {checks, configured, current} — the setup doctor
export const settingsOpen = writable(false);    // Settings/config view manually opened
export const vfrWeather   = writable({ busy: false, ok: null, message: '' });  // "VFR day" button state

// Audio
export const pttActive       = writable(false);   // mic is recording
export const transcription   = writable('');      // last STT preview text
export const audioEnabled    = writable(false);   // set true once mic permission granted

// Derived: is the aircraft on the ground?
export const onGround = derived(flightState, $s => $s?.on_ground ?? true);

// Derived: highlight which airport freq matches COM1
export const com1Mhz = derived(flightState, $s => $s?.com1_mhz ?? 0);
export const com2Mhz = derived(flightState, $s => $s?.com2_mhz ?? 0);

// Derived: live weather from X-Plane
export const qnhHpa   = derived(flightState, $s => $s?.qnh_hpa  ?? 0);
export const windDir  = derived(flightState, $s => $s?.wind_dir  ?? 0);
export const windKts  = derived(flightState, $s => $s?.wind_kts  ?? 0);
