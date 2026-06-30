<script>
  import { scenarioDrawerOpen, source } from './store.js';
  import { loadScenario, setSource } from './ws.js';

  // Built-in presets
  const PRESETS = [
    {
      name: 'EDDV → EDDG — VFR Journey',
      data: {
        name: 'EDDV Standard Departure',
        description: 'C172 D-EIYD on the GA apron at Hannover, routing to Münster/Osnabrück. Wind 270/8, QNH 1018, ATIS Charlie.',
        aircraft: { icao: 'C172', callsign: 'D-EIYD' },
        departure_airport: 'EDDV',
        destination_airport: 'EDDG',
        route: 'EDDV OSN EDDG',
        conditions: { qnh: 1018, wind_dir: 270, wind_kts: 8, visibility_km: 10, atis: 'Charlie' },
        position: { lat: 52.4608, lon: 9.6900, alt_ft: 183, heading: 270, on_ground: true },
      }
    },
    {
      name: 'EDDG — Arrival from East',
      data: {
        name: 'EDDG Arrival from East',
        description: 'C172 D-EIYD inbound to Münster/Osnabrück, 10nm east. Wind 250/6, QNH 1018.',
        aircraft: { icao: 'C172', callsign: 'D-EIYD' },
        departure_airport: 'EDDG',
        conditions: { qnh: 1018, wind_dir: 250, wind_kts: 6, visibility_km: 15, atis: 'Delta' },
        position: { lat: 52.134, lon: 7.900, alt_ft: 2500, heading: 270, on_ground: false },
      }
    },
  ];

  // Form state
  let aircraftIcao = $state('C172');
  let callsign     = $state('D-EIYD');
  let depAirport   = $state('EDDV');
  let destAirport  = $state('EDDG');
  let route        = $state('EDDV OSN EDDG');
  let qnh          = $state(1018);
  let windDir      = $state(270);
  let windKts      = $state(8);
  let vis          = $state(10);
  let atis         = $state('Charlie');
  let onGround     = $state(true);

  function applyPreset(preset) {
    const d = preset.data;
    aircraftIcao = d.aircraft.icao;
    callsign     = d.aircraft.callsign;
    depAirport   = d.departure_airport;
    destAirport  = d.destination_airport ?? '';
    route        = d.route ?? '';
    qnh          = d.conditions.qnh;
    windDir      = d.conditions.wind_dir;
    windKts      = d.conditions.wind_kts;
    vis          = d.conditions.visibility_km;
    atis         = d.conditions.atis;
    onGround     = d.position.on_ground;
  }

  function buildScenario() {
    // Position: if a known airport, backend will snap to centroid.
    // We pass 0,0 and the backend uses departure_airport ICAO for lookup.
    const dep  = depAirport.trim().toUpperCase();
    const dest = destAirport.trim().toUpperCase();
    // A route wins if given; otherwise a destination alone implies a direct leg.
    const rte  = route.trim().toUpperCase() || (dest ? `${dep} ${dest}` : '');
    const scn = {
      name: dest ? `${dep} → ${dest}` : `${dep} — Custom`,
      description: dest ? `${aircraftIcao} ${callsign}, ${dep} to ${dest}`
                        : `${aircraftIcao} ${callsign} at ${dep}`,
      aircraft: { icao: aircraftIcao, callsign },
      departure_airport: dep,
      conditions: { qnh: Number(qnh), wind_dir: Number(windDir), wind_kts: Number(windKts),
                    visibility_km: Number(vis), atis },
      position: { lat: 0, lon: 0, alt_ft: 0, heading: Number(windDir) > 180 ? Number(windDir) - 180 : Number(windDir) + 180, on_ground: onGround },
    };
    if (dest) scn.destination_airport = dest;
    if (rte)  scn.route = rte;
    return scn;
  }

  function load() {
    loadScenario(buildScenario());
    scenarioDrawerOpen.set(false);
  }

  function close() {
    scenarioDrawerOpen.set(false);
  }

  function connectXPlane() {
    setSource('xplane');
    scenarioDrawerOpen.set(false);
  }
</script>

<!-- Backdrop -->
<div class="backdrop" onclick={close} role="presentation"></div>

<div class="drawer">
  <div class="drawer-header">
    <span class="drawer-title"><span class="gi" data-tech="▸">⚙</span> Scenario Setup</span>
    <button class="close-btn" onclick={close}>✕</button>
  </div>

  <!-- Source toggle -->
  <div class="section">
    <div class="section-label">DATA SOURCE</div>
    <div class="source-row">
      <button class="source-btn" class:active={$source === 'simulated'}
              onclick={() => {}}>Simulated</button>
      <button class="source-btn" class:active={$source === 'xplane'}
              onclick={connectXPlane}>X-Plane</button>
    </div>
  </div>

  <div class="divider"></div>

  <!-- Presets -->
  <div class="section">
    <div class="section-label">PRESETS</div>
    {#each PRESETS as preset}
      <button class="preset-btn" onclick={() => applyPreset(preset)}>
        {preset.name}
      </button>
    {/each}
  </div>

  <div class="divider"></div>

  <!-- Manual setup -->
  <div class="section">
    <div class="section-label">AIRCRAFT</div>
    <div class="field-row">
      <label>Type<input type="text" bind:value={aircraftIcao} maxlength="4" placeholder="C172" /></label>
      <label>Callsign<input type="text" bind:value={callsign} placeholder="D-EIYD" /></label>
    </div>
  </div>

  <div class="section">
    <div class="section-label">ROUTE</div>
    <div class="field-row">
      <label>Departure ICAO<input type="text" bind:value={depAirport} maxlength="4" placeholder="EDDV" /></label>
      <label>Destination ICAO<input type="text" bind:value={destAirport} maxlength="4" placeholder="EDDG" /></label>
    </div>
    <label class="field-full">
      Route <span class="hint">(optional — via navaids/fixes)</span>
      <input type="text" bind:value={route} placeholder="EDDV OSN EDDG" />
    </label>
  </div>

  <div class="section">
    <div class="section-label">WEATHER</div>
    <div class="field-row">
      <label>QNH<input type="number" bind:value={qnh} min="900" max="1100" /></label>
      <label>ATIS
        <select bind:value={atis} class="select-field">
          {#each 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('') as letter}
            <option value={letter}>{letter}</option>
          {/each}
        </select>
      </label>
    </div>
    <div class="field-row">
      <label>Wind dir<input type="number" bind:value={windDir} min="0" max="360" /></label>
      <label>Wind kt<input type="number" bind:value={windKts} min="0" max="99" /></label>
    </div>
    <label class="field-full">
      Visibility km
      <input type="number" bind:value={vis} min="0" max="99" />
    </label>
  </div>

  <div class="section">
    <div class="section-label">POSITION</div>
    <label class="checkbox-row">
      <input type="checkbox" bind:checked={onGround} />
      On ground at departure airport
    </label>
  </div>

  <div class="spacer"></div>

  <button class="load-btn" onclick={load}>Load Scenario</button>
</div>

<style>
  .backdrop {
    position: fixed; inset: 0;
    background: rgba(0,0,0,0.5);
    z-index: 10;
  }

  .drawer {
    position: fixed;
    right: 0; top: 0; bottom: 0;
    width: 320px;
    background: var(--bg-panel);
    border-left: 1px solid var(--border);
    z-index: 11;
    display: flex;
    flex-direction: column;
    overflow-y: auto;
    padding: 0 0 16px;
  }

  .drawer-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 16px;
    border-bottom: 1px solid var(--border);
    position: sticky;
    top: 0;
    background: var(--bg-panel);
    z-index: 1;
  }
  .drawer-title { font-weight: 700; font-size: 13px; }
  .close-btn {
    background: none;
    color: var(--text-muted);
    font-size: 14px;
    padding: 2px 6px;
    border-radius: var(--radius);
  }
  .close-btn:hover { color: var(--text); background: var(--bg-input); }

  .section { padding: 12px 16px 4px; display: flex; flex-direction: column; gap: 6px; }
  .section-label {
    font-size: 10px; font-weight: 700;
    letter-spacing: 0.1em; color: var(--text-dim);
    padding-bottom: 2px;
  }

  .divider { height: 1px; background: var(--border); margin: 6px 0; }

  .source-row { display: flex; gap: 6px; }
  .source-btn {
    flex: 1; padding: 6px; background: var(--bg-input);
    border: 1px solid var(--border); border-radius: var(--radius);
    color: var(--text-muted); font-size: 12px;
    transition: all 0.15s;
  }
  .source-btn.active {
    border-color: var(--accent-blue);
    color: var(--accent-blue);
    background: rgba(88,166,255,0.1);
  }

  .preset-btn {
    padding: 7px 10px;
    background: var(--bg-input);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    color: var(--text-muted);
    text-align: left;
    font-size: 12px;
    transition: border-color 0.15s, color 0.15s;
  }
  .preset-btn:hover { border-color: var(--accent-blue); color: var(--text); }

  label { display: flex; flex-direction: column; gap: 4px; font-size: 11px; color: var(--text-muted); }
  .hint { color: var(--text-dim); font-size: 10px; }
  input[type="text"], input[type="number"] { padding: 5px 8px; width: 100%; }
  .select-field {
    background: var(--bg-input); color: var(--text);
    border: 1px solid var(--border); border-radius: var(--radius);
    padding: 5px 8px; font-family: inherit; font-size: 13px;
  }

  .field-row { display: flex; gap: 10px; }
  .field-row label { flex: 1; }
  .field-full { width: 100%; }

  .checkbox-row {
    flex-direction: row !important;
    align-items: center;
    gap: 8px;
    color: var(--text-muted);
    cursor: pointer;
  }

  .spacer { flex: 1; min-height: 16px; }

  .load-btn {
    margin: 0 16px;
    padding: 10px;
    background: var(--accent-green);
    color: #000;
    border-radius: var(--radius);
    font-weight: 700;
    font-size: 13px;
    letter-spacing: 0.04em;
    transition: opacity 0.15s;
  }
  .load-btn:hover { opacity: 0.85; }
</style>
