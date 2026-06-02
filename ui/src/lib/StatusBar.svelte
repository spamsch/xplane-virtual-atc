<script>
  import { wsStatus, source, scenarioName, phase, station, atcCallsign, backendUptime } from './store.js';

  const phaseLabel = {
    PRE_DEPARTURE: 'Pre-departure', GROUND_DEPARTURE: 'Ground', TAXIING: 'Taxiing',
    DEPARTING: 'Departing', EN_ROUTE: 'En route', EN_ROUTE_FIS: 'En route (FIS)',
    APPROACH: 'Approach', CIRCUIT: 'Circuit', GROUND_ARRIVAL: 'Ground (arr.)', PARKED: 'Parked',
  };
  const stationLabel = { GND: 'GND', TWR: 'TWR', APP: 'APP', DEP: 'DEP', RADAR: 'RAD', FIS: 'FIS' };

  function fmtUptime(s) {
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    if (h > 0) return `${h}h ${m}m`;
    if (m > 0) return `${m}m ${sec}s`;
    return `${sec}s`;
  }
</script>

<header class="status-bar">
  <!-- Left: connection status -->
  <div class="group">
    <span class="dot" class:green={$wsStatus === 'connected'}
                      class:amber={$wsStatus === 'connecting'}
                      class:red={$wsStatus === 'error' || $wsStatus === 'disconnected'}></span>
    {#if $wsStatus === 'connected'}
      <span class="label">Backend {fmtUptime($backendUptime)}</span>
    {:else}
      <span class="label muted">{$wsStatus}</span>
    {/if}
  </div>

  <!-- Center: source + station -->
  <div class="group center">
    <span class="tag" class:simulated={$source === 'simulated'} class:xplane={$source === 'xplane'}>
      {$source === 'xplane' ? 'X-PLANE' : 'SIMULATED'}
    </span>
    {#if $scenarioName}
      <span class="scenario-name">{$scenarioName}</span>
    {/if}
  </div>

  <!-- Right: ATC callsign + phase -->
  <div class="group right">
    <span class="station-badge">{stationLabel[$station] ?? $station}</span>
    <span class="callsign">{$atcCallsign}</span>
    <span class="phase-label muted">{phaseLabel[$phase] ?? $phase}</span>
  </div>
</header>

<style>
  .status-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: 36px;
    padding: 0 12px;
    background: var(--bg-panel);
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
    gap: 8px;
  }

  .group { display: flex; align-items: center; gap: 8px; }
  .center { flex: 1; justify-content: center; }
  .right { justify-content: flex-end; }

  .dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
  }
  .dot.green  { background: var(--accent-green); box-shadow: 0 0 6px var(--accent-green); }
  .dot.amber  { background: var(--accent-amber); }
  .dot.red    { background: var(--accent-red); }

  .label  { color: var(--text); }
  .muted  { color: var(--text-muted); }

  .tag {
    padding: 2px 8px;
    border-radius: var(--radius);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.05em;
  }
  .tag.simulated { background: rgba(188, 140, 255, 0.15); color: var(--accent-purple); }
  .tag.xplane    { background: rgba(63, 185, 80, 0.15);  color: var(--accent-green); }

  .scenario-name { color: var(--text-muted); font-size: 11px; }

  .station-badge {
    background: var(--bg-input);
    border: 1px solid var(--border-bright);
    border-radius: var(--radius);
    padding: 1px 6px;
    font-size: 11px;
    font-weight: 700;
    color: var(--accent-blue);
  }

  .callsign { color: var(--text); font-weight: 600; }
  .phase-label { font-size: 11px; }
</style>
