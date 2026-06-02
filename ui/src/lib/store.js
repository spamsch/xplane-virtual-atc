import { writable, derived } from 'svelte/store';

// Connection
export const wsStatus = writable('disconnected'); // 'disconnected'|'connecting'|'connected'|'error'
export const backendUptime = writable(0);
export const source = writable('simulated');       // 'xplane'|'simulated'
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

// Audio
export const pttActive       = writable(false);   // mic is recording
export const transcription   = writable('');      // last STT preview text
export const audioEnabled    = writable(false);   // set true once mic permission granted

// Derived: is the aircraft on the ground?
export const onGround = derived(flightState, $s => $s?.on_ground ?? true);

// Derived: highlight which airport freq matches COM1
export const com1Mhz = derived(flightState, $s => $s?.com1_mhz ?? 0);
export const com2Mhz = derived(flightState, $s => $s?.com2_mhz ?? 0);
