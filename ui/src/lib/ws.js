/**
 * WebSocket client with automatic reconnect.
 * Dispatches incoming messages to the Svelte stores.
 */

import {
  wsStatus, backendUptime, source, scenarioName,
  flightState, airport, activeRunway, atcCallsign, boundaryNotes,
  messages, phase, station, thinking
} from './store.js';

const WS_URL = 'ws://localhost:8765';
const RECONNECT_DELAY_MS = 3000;

let ws = null;
let reconnectTimer = null;

function dispatch(msg) {
  switch (msg.type) {
    case 'backend_status':
      backendUptime.set(msg.uptime_s);
      source.set(msg.source);
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

    case 'error':
      console.error('[backend]', msg.message);
      // Show as a system message in the chat
      messages.update(list => [...list, {
        role: 'system',
        text: `⚠ ${msg.message}`,
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

export function sendTransmission(text) {
  sendMessage('pilot_transmission', { text });
}

export function loadScenario(scenarioObj) {
  sendMessage('load_scenario', { scenario: scenarioObj });
}

export function setSource(src) {
  sendMessage('set_source', { source: src });
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
