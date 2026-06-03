<script>
  import { airport, activeRunway, com1Mhz, com2Mhz, scenarioDrawerOpen } from './store.js';
  import { sendMessage } from './ws.js';

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
</style>
