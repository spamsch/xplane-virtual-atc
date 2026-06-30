<script>
  import { airport, activeRunway, com1Mhz, com2Mhz, scenarioDrawerOpen,
           xplaneConnected, vfrWeather, flightplan, flightplanStage } from './store.js';
  import { sendMessage, setVfrWeather } from './ws.js';

  // Flight-plan services — the valid frequencies for the whole journey, with the
  // leg that's working you right now highlighted as the service "switches" from
  // the departure field to the en-route FIS to the arrival field.
  const STAGE_LABEL = { departure: 'DEP', enroute: 'FIS', arrival: 'ARR' };
  $: fpServices = $flightplan?.services ?? [];
  function stageActive(stage) {
    const s = $flightplanStage;
    if (!s) return false;
    if (s === 'arrival_ground') return stage === 'arrival';
    return s === stage;
  }
  function isCom1Freq(f) { return f && Math.abs(f - $com1Mhz) < 0.005; }

  const freqTypeOrder = [53, 54, 50, 52, 55, 56, 51];  // GND, TWR, ATIS, CLD, APP, DEP, CTAF
  const freqTypeShort = {
    50: 'ATIS', 51: 'CTAF', 52: 'CLD', 53: 'GND',
    54: 'TWR',  55: 'APP',  56: 'DEP',
  };

  $: ap = $airport;
  $: sortedFreqs = ap ? [...ap.frequencies].sort((a, b) => {
    const ia = freqTypeOrder.indexOf(a.type_code);
    const ib = freqTypeOrder.indexOf(b.type_code);
    return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
  }) : [];

  function isCom1(freqMhz) { return Math.abs(freqMhz - $com1Mhz) < 0.005; }
  function isCom2(freqMhz) { return Math.abs(freqMhz - $com2Mhz) < 0.005; }

  function isActive(rwy) {
    if (!$activeRunway) return false;
    return rwy.name1 === $activeRunway || rwy.name2 === $activeRunway;
  }

  function tuneCom1(freqMhz) { sendMessage('tune_com1', { freq_mhz: freqMhz }); }
  function tuneCom2(e, freqMhz) {
    e.preventDefault();
    sendMessage('tune_com2', { freq_mhz: freqMhz });
  }
</script>

<aside class="airport-panel">
  {#if fpServices.length}
    <!-- Flight-plan services — the active leg is highlighted; click to tune COM1 -->
    <section class="fp-services">
      <div class="section-label">FLIGHT PLAN</div>
      {#each fpServices as svc}
        <button class="fp-row" class:active={stageActive(svc.stage)}
                disabled={!svc.freq_mhz}
                onclick={() => svc.freq_mhz && tuneCom1(svc.freq_mhz)}
                title={svc.freq_mhz ? 'Tune COM1' : 'No published frequency'}>
          <span class="fp-stage">{STAGE_LABEL[svc.stage] ?? svc.stage}</span>
          <span class="fp-name">{svc.label}</span>
          <span class="fp-freq" class:com1={isCom1Freq(svc.freq_mhz)}>
            {svc.freq_mhz ? svc.freq_mhz.toFixed(3) : '—'}
          </span>
        </button>
      {/each}
    </section>
    <div class="divider"></div>
  {/if}

  {#if ap}
    <!-- Airport identity -->
    <section class="identity">
      <div class="icao">{ap.icao}</div>
      <div class="name">{ap.name}</div>
      <div class="elev muted">Elev {ap.elevation_ft} ft</div>
    </section>

    <div class="divider"></div>

    <!-- Runways -->
    <section>
      <div class="section-label">RUNWAYS</div>
      {#each ap.runways as rwy}
        <div class="runway-row" class:active-rwy={isActive(rwy)}>
          {#if isActive(rwy)}<span class="rwy-arrow">▶</span>{:else}<span class="rwy-arrow dim"> </span>{/if}
          <span class="rwy-name">{rwy.name1}<span class="dim">/</span>{rwy.name2}</span>
          <span class="rwy-width muted">{rwy.width_m}m</span>
        </div>
      {/each}
    </section>

    <div class="divider"></div>

    <!-- Frequencies -->
    <section class="freq-section">
      <div class="section-label-row">
        <span class="section-label">FREQUENCIES</span>
        <span class="freq-hint">L·COM1 R·COM2</span>
      </div>
      {#each sortedFreqs as f}
        <div class="freq-row">
          <span class="freq-type">{freqTypeShort[f.type_code] ?? f.type_name}</span>
          <button
            class="freq-mhz"
            class:com1={isCom1(f.freq_mhz)}
            class:com2={isCom2(f.freq_mhz)}
            onclick={() => tuneCom1(f.freq_mhz)}
            oncontextmenu={(e) => tuneCom2(e, f.freq_mhz)}
            title="Left-click: COM1 · Right-click: COM2"
          >
            {f.freq_mhz.toFixed(3)}
          </button>
        </div>
      {/each}
      {#if !sortedFreqs.some(f => Math.abs(f.freq_mhz - 122.800) < 0.005)}
        <div class="freq-row unicom-row">
          <span class="freq-type unicom-label">UNICOM</span>
          <button
            class="freq-mhz"
            class:com1={isCom1(122.800)}
            class:com2={isCom2(122.800)}
            onclick={() => tuneCom1(122.800)}
            oncontextmenu={(e) => tuneCom2(e, 122.800)}
            title="Left-click: COM1 · Right-click: COM2"
          >
            122.800
          </button>
        </div>
      {/if}
    </section>

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

  .identity { padding: 4px 0 8px; }
  .icao { font-size: 28px; font-weight: 700; letter-spacing: 0.06em; line-height: 1.1; }
  .name { font-size: 13px; color: var(--text-muted); padding-top: 2px; }
  .elev { font-size: 11px; padding-top: 2px; }
  .muted { color: var(--text-muted); }
  .dim   { color: var(--text-dim); }

  .divider { height: 1px; background: var(--border); margin: 10px 0; }

  .section-label {
    font-size: 10px; font-weight: 700;
    letter-spacing: 0.1em; color: var(--text-dim);
    padding-bottom: 6px;
  }

  .runway-row {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 3px 0;
    font-size: 12px;
  }
  .rwy-arrow { color: var(--accent-green); font-size: 10px; width: 10px; flex-shrink: 0; }
  .rwy-name  { font-weight: 600; }
  .rwy-width { font-size: 10px; margin-left: auto; }
  .active-rwy .rwy-name { color: var(--accent-green); }

  /* Flight-plan service ladder */
  .fp-services { display: flex; flex-direction: column; gap: 4px; }
  .fp-row {
    display: flex;
    align-items: baseline;
    gap: 8px;
    width: 100%;
    padding: 5px 6px;
    background: var(--bg-input);
    border: 1px solid var(--border);
    border-left: 2px solid transparent;
    border-radius: var(--radius);
    text-align: left;
    transition: border-color 0.12s, background 0.12s;
  }
  .fp-row:not(:disabled):hover { border-color: var(--border-bright); }
  .fp-row:disabled { opacity: 0.55; cursor: default; }
  .fp-row.active {
    border-left-color: var(--accent-green);
    background: rgba(63, 185, 80, 0.08);
  }
  .fp-stage {
    font-size: 9px; font-weight: 700; letter-spacing: 0.08em;
    color: var(--text-dim); width: 26px; flex-shrink: 0;
  }
  .fp-row.active .fp-stage { color: var(--accent-green); }
  .fp-name {
    font-size: 12px; color: var(--text-muted);
    flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .fp-row.active .fp-name { color: var(--text); }
  .fp-freq { font-size: 13px; color: var(--text-muted); flex-shrink: 0; }
  .fp-freq.com1 { color: var(--accent-green); font-weight: 600; }

  .freq-section { display: flex; flex-direction: column; gap: 4px; }

  .section-label-row {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    padding-bottom: 6px;
  }
  .section-label-row .section-label { padding-bottom: 0; }
  .freq-hint {
    font-size: 9px;
    color: var(--text-dim);
    letter-spacing: 0.04em;
  }

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
    display: flex; align-items: center; gap: 4px;
    background: none;
    padding: 2px 4px;
    border-radius: var(--radius);
    transition: background 0.1s, color 0.1s;
    cursor: pointer;
  }
  .freq-mhz:hover  { background: var(--bg-panel-alt); color: var(--text); }
  .freq-mhz.com1 { color: var(--accent-green); font-weight: 600; }
  .freq-mhz.com2 { color: var(--accent-amber); font-weight: 600; }

  .unicom-row { margin-top: 6px; border-top: 1px solid var(--border); padding-top: 6px; }
  .unicom-label { color: var(--text-dim); }

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
