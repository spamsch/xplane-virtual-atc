<script>
  import { airport, journey, com1Mhz, scenarioDrawerOpen, xplaneConnected,
           vfrWeather, flightplanStage, phase, onGround } from './store.js';
  import { sendMessage, setVfrWeather } from './ws.js';
  import FrequencyList from './FrequencyList.svelte';

  // The right-hand panel switches its frequency view between the legs of a
  // journey — Departure field, en-route FIS, and (when a destination is known)
  // the Arrival field. A flight plan, a scenario, or a free simulated flight all
  // populate `journey`; without a departure at all it falls back to the single
  // detected airport.
  $: j = $journey;
  $: hasJourney = !!(j && j.departure);

  // Segments depend on what's known: DEP and FIS always, ARR only with a filed
  // or scenario destination.
  $: SEGMENTS = [
    { view: 'departure', code: 'DEP', sub: j?.departure?.icao ?? '—' },
    { view: 'enroute',   code: 'FIS', sub: 'FIS' },
    ...(j?.arrival ? [{ view: 'arrival', code: 'ARR', sub: j.arrival.icao }] : []),
  ];

  // Which leg are we actually flying? flightplanStage is authoritative when a
  // plan is loaded; otherwise derive it from the session phase, and — for a free
  // flight where the phase never advances — treat simply being airborne as the
  // en-route leg so the marker tracks reality.
  const ARRIVAL_PHASES = ['approach', 'circuit', 'ground_arrival', 'parked'];
  const ENROUTE_PHASES = ['departing', 'en_route', 'en_route_fis'];
  function legFromStage(s) {
    if (s === 'departure') return 'departure';
    if (s === 'enroute') return 'enroute';
    if (s === 'arrival' || s === 'arrival_ground') return 'arrival';
    return null;
  }
  $: activeLeg = legFromStage($flightplanStage)
    ?? (ARRIVAL_PHASES.includes($phase) ? 'arrival'
        : (ENROUTE_PHASES.includes($phase) || !$onGround) ? 'enroute'
        : 'departure');

  // selectedView auto-follows the leg you're flying, but a manual pick sticks
  // until the active leg actually changes (then it re-syncs).
  let selectedView = 'departure';
  let _lastActiveLeg = null;
  $: if (hasJourney && activeLeg !== _lastActiveLeg) {
    _lastActiveLeg = activeLeg;
    selectedView = activeLeg;
  }

  $: fis = j?.fis ?? null;
  // Match at the radio's real 10 kHz resolution (see FrequencyList).
  function isCom1(freqMhz) { return !!freqMhz && Math.round(freqMhz * 100) === Math.round($com1Mhz * 100); }
  function tuneCom1(freqMhz) { if (freqMhz) sendMessage('tune_com1', { freq_mhz: freqMhz }); }
</script>

<aside class="airport-panel">
  {#if hasJourney}
    <!-- Leg selector — DEP · FIS · ARR. The leg you're flying is marked; click any
         to read its frequencies. -->
    <div class="seg-row">
      {#each SEGMENTS as s}
        <button class="seg" class:selected={selectedView === s.view}
                class:active={activeLeg === s.view}
                onclick={() => selectedView = s.view}>
          {#if activeLeg === s.view}<span class="seg-dot"></span>{/if}
          <span class="seg-code">{s.code}</span>
          <span class="seg-sub">{s.sub}</span>
        </button>
      {/each}
    </div>

    <div class="divider"></div>

    {#if selectedView === 'enroute'}
      <!-- FIS leg has no airport record — just a regional information service. -->
      <section class="identity">
        <div class="icao">FIS</div>
        <div class="name">{fis?.callsign ?? 'Information'}</div>
        <div class="elev muted">En-route flight information service</div>
      </section>
      <div class="divider"></div>
      <section class="freq-section">
        <div class="section-label-row">
          <span class="section-label">FREQUENCY</span>
          <span class="freq-hint">L·COM1</span>
        </div>
        <div class="freq-row">
          <span class="freq-type">FIS</span>
          <button class="freq-mhz" class:com1={isCom1(fis?.freq_mhz)}
                  disabled={!fis?.freq_mhz}
                  onclick={() => tuneCom1(fis?.freq_mhz)}
                  title={fis?.freq_mhz ? 'Tune COM1' : 'No published frequency'}>
            {fis?.freq_mhz ? fis.freq_mhz.toFixed(3) : '—'}
          </button>
        </div>
      </section>
    {:else if selectedView === 'arrival' && j.arrival}
      <FrequencyList airport={j.arrival} />
    {:else}
      <FrequencyList airport={j.departure} />
    {/if}

  {:else if $airport}
    <!-- No journey staged — the single detected field. -->
    <FrequencyList airport={$airport} />
  {:else}
    <div class="no-airport">
      <div class="no-airport-icon gi">✈</div>
      <div class="muted">No airport detected</div>
    </div>
  {/if}

  <div class="spacer"></div>

  <!-- VFR day weather (live X-Plane only): real wind/pressure/temp, scattered
       clouds, visibility > 5 sm, local noon. -->
  {#if $xplaneConnected}
    <button class="vfr-btn" onclick={setVfrWeather} disabled={$vfrWeather.busy}
            title="Download real weather, keep wind/pressure/temp, set scattered clouds, visibility >5 sm, and local noon">
      {$vfrWeather.busy ? 'Setting weather…' : 'VFR Day'}
    </button>
    {#if $vfrWeather.message && !$vfrWeather.busy}
      <div class="vfr-msg" class:bad={$vfrWeather.ok === false}>{$vfrWeather.message}</div>
    {/if}
  {/if}

  <!-- Scenario button -->
  <button class="scenario-btn" onclick={() => scenarioDrawerOpen.set(true)}>
    <span class="gi" data-tech="▸">⚙</span> Scenario
  </button>
</aside>

<style>
  .airport-panel {
    background: var(--bg-panel);
    display: flex;
    flex-direction: column;
    overflow-y: auto;
    padding: 12px;
  }

  .muted { color: var(--text-muted); }
  .divider { height: 1px; background: var(--border); margin: 10px 0; }

  /* Leg selector */
  .seg-row { display: flex; gap: 4px; }
  .seg {
    position: relative;
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 1px;
    padding: 6px 2px 5px;
    background: var(--bg-input);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    color: var(--text-muted);
    transition: border-color 0.12s, background 0.12s, color 0.12s;
  }
  .seg:hover { border-color: var(--border-bright); color: var(--text); }
  .seg.selected {
    background: var(--bg-panel-alt);
    border-color: var(--border-bright);
    color: var(--text);
  }
  .seg-code { font-size: 11px; font-weight: 700; letter-spacing: 0.08em; }
  .seg-sub  { font-size: 9px; color: var(--text-dim); letter-spacing: 0.02em; }
  .seg.selected .seg-sub { color: var(--text-muted); }
  .seg.active .seg-code { color: var(--accent-green); }
  .seg-dot {
    position: absolute;
    top: 4px; right: 4px;
    width: 5px; height: 5px;
    border-radius: 50%;
    background: var(--accent-green);
  }

  /* FIS detail block (mirrors FrequencyList's identity/freq styling) */
  .identity { padding: 4px 0 8px; }
  .icao { font-size: 28px; font-weight: 700; letter-spacing: 0.06em; line-height: 1.1; }
  .name { font-size: 13px; color: var(--text-muted); padding-top: 2px; }
  .elev { font-size: 11px; padding-top: 2px; }

  .freq-section { display: flex; flex-direction: column; gap: 4px; }
  .section-label-row {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    padding-bottom: 6px;
  }
  .section-label {
    font-size: 10px; font-weight: 700;
    letter-spacing: 0.1em; color: var(--text-dim);
  }
  .freq-hint { font-size: 9px; color: var(--text-dim); letter-spacing: 0.04em; }
  .freq-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 3px 0;
  }
  .freq-type {
    font-size: 13px; font-weight: 700;
    letter-spacing: 0.04em; color: var(--text-muted);
    width: 44px; flex-shrink: 0;
  }
  .freq-mhz {
    color: var(--text-muted);
    font-size: 15px;
    background: none;
    padding: 2px 4px;
    border-radius: var(--radius);
    transition: background 0.1s, color 0.1s;
    cursor: pointer;
  }
  .freq-mhz:not(:disabled):hover { background: var(--bg-panel-alt); color: var(--text); }
  .freq-mhz:disabled { opacity: 0.55; cursor: default; }
  .freq-mhz.com1 { color: var(--accent-green); font-weight: 600; }

  .no-airport {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 8px;
  }
  .no-airport-icon { font-size: 32px; color: var(--text-dim); }

  .spacer { flex: 1; }

  .scenario-btn {
    width: 100%;
    padding: 8px;
    background: var(--bg-input);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    color: var(--text-muted);
    font-size: 12px;
    margin-top: 10px;
    transition: border-color 0.15s, color 0.15s;
  }
  .scenario-btn:hover {
    border-color: var(--accent-blue);
    color: var(--accent-blue);
  }

  .vfr-btn {
    width: 100%;
    padding: 8px;
    background: var(--bg-input);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    color: var(--text-muted);
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.04em;
    transition: border-color 0.15s, color 0.15s;
  }
  .vfr-btn:not(:disabled):hover { border-color: var(--accent-amber); color: var(--accent-amber); }
  .vfr-btn:disabled { opacity: 0.6; cursor: default; }
  .vfr-msg {
    font-size: 10px;
    line-height: 1.4;
    color: var(--text-muted);
    margin-top: 6px;
  }
  .vfr-msg.bad { color: var(--accent-red); }
</style>
