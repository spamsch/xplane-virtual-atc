<script>
  import { airport, activeRunway, com1Mhz, com2Mhz, scenarioDrawerOpen } from './store.js';

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

  function isTuned(freqMhz) {
    return (Math.abs(freqMhz - $com1Mhz) < 0.005) ||
           (Math.abs(freqMhz - $com2Mhz) < 0.005);
  }

  function isActive(rwy) {
    if (!$activeRunway) return false;
    return rwy.name1 === $activeRunway || rwy.name2 === $activeRunway;
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
      <div class="section-label">FREQUENCIES</div>
      {#each sortedFreqs as f}
        <div class="freq-row" class:tuned={isTuned(f.freq_mhz)}>
          <span class="freq-type">{freqTypeShort[f.type_code] ?? f.type_name}</span>
          <span class="freq-mhz" class:tuned={isTuned(f.freq_mhz)}>
            {#if isTuned(f.freq_mhz)}<span class="dot-tuned">●</span>{/if}
            {f.freq_mhz.toFixed(3)}
          </span>
        </div>
      {/each}
    </section>

  {:else}
    <div class="no-airport">
      <div class="no-airport-icon">✈</div>
      <div class="muted">No airport detected</div>
    </div>
  {/if}

  <div class="spacer"></div>

  <!-- Scenario button -->
  <button class="scenario-btn" onclick={() => scenarioDrawerOpen.set(true)}>
    ⚙ Scenario
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
    font-size: 13px;
    display: flex; align-items: center; gap: 4px;
  }
  .freq-mhz.tuned { color: var(--accent-green); font-weight: 600; }
  .dot-tuned { font-size: 9px; }

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
