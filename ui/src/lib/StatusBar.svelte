<script>
  import { wsStatus, source, scenarioName, phase, station, atcCallsign, backendUptime,
           xplaneConnected, settingsOpen, flightplanOpen, flightplan, flightplanStage } from './store.js';
  import { theme, THEMES, cycleTheme } from './theme.js';

  const stageLabel = { departure: 'DEP', enroute: 'EN ROUTE', arrival: 'ARR', arrival_ground: 'GND' };

  $: themeLabel = THEMES.find((t) => t.key === $theme)?.label ?? 'THEME';

  const phaseLabel = {
    PRE_DEPARTURE: 'Pre-departure', GROUND_DEPARTURE: 'Ground', TAXIING: 'Taxiing',
    DEPARTING: 'Departing', EN_ROUTE: 'En route', EN_ROUTE_FIS: 'En route (FIS)',
    APPROACH: 'Approach', CIRCUIT: 'Circuit', GROUND_ARRIVAL: 'Ground (arr.)', PARKED: 'Parked',
  };
  const stationLabel = { GND: 'GND', TWR: 'TWR', APP: 'APP', DEP: 'DEP', RADAR: 'RAD', FIS: 'FIS', CTAF: 'INFO' };

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
    {#if $source === 'xplane'}
      <span class="link" class:linked={$xplaneConnected} class:nolink={!$xplaneConnected}>
        <span class="dot" class:green={$xplaneConnected} class:red={!$xplaneConnected}></span>
        {$xplaneConnected ? 'LINKED' : 'NO LINK'}
      </span>
    {/if}
    {#if $scenarioName}
      <span class="scenario-name">{$scenarioName}</span>
    {/if}
    {#if $flightplan}
      <button class="fpl-active" onclick={() => flightplanOpen.set(true)}
              title="Open the flight plan">
        {$flightplan.summary}
        {#if $flightplanStage}<span class="fpl-stage">{stageLabel[$flightplanStage] ?? $flightplanStage}</span>{/if}
      </button>
    {/if}
  </div>

  <!-- Right: ATC callsign + phase + settings -->
  <div class="group right">
    <span class="station-badge">{stationLabel[$station] ?? $station}</span>
    <span class="callsign">{$atcCallsign}</span>
    <span class="phase-label muted">{phaseLabel[$phase] ?? $phase}</span>
    <button class="fpl-btn" onclick={() => flightplanOpen.set(true)} title="Flight plan">FPL</button>
    <button class="theme-btn" onclick={cycleTheme} title="Switch UI theme">{themeLabel}</button>
    <button class="settings-btn" onclick={() => settingsOpen.set(true)} title="Settings"><span class="gi" data-tech="CFG">⚙</span></button>
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

  .link {
    display: inline-flex; align-items: center; gap: 5px;
    font-size: 11px; font-weight: 700; letter-spacing: 0.05em;
  }
  .link.linked { color: var(--accent-green); }
  .link.nolink { color: var(--accent-red); }
  .link.nolink .dot { animation: blink 1.2s steps(2, start) infinite; }
  @keyframes blink { 50% { opacity: 0.25; } }

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
  .theme-btn {
    background: var(--bg-input);
    border: 1px solid var(--border);
    color: var(--text-muted);
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.08em;
    padding: 2px 7px;
    border-radius: var(--radius);
    transition: color 0.15s, border-color 0.15s;
  }
  .theme-btn:hover { color: var(--accent-blue); border-color: var(--border-bright); }
  .fpl-btn {
    background: var(--bg-input); border: 1px solid var(--border);
    color: var(--text-muted); font-size: 10px; font-weight: 700;
    letter-spacing: 0.08em; padding: 2px 7px; border-radius: var(--radius);
    transition: color 0.15s, border-color 0.15s;
  }
  .fpl-btn:hover { color: var(--accent-green); border-color: var(--border-bright); }
  .fpl-active {
    display: inline-flex; align-items: center; gap: 6px;
    background: rgba(63,185,80,0.10); border: 1px solid rgba(63,185,80,0.35);
    color: var(--accent-green); font-size: 11px; font-weight: 700;
    letter-spacing: 0.04em; padding: 1px 7px; border-radius: var(--radius);
  }
  .fpl-active:hover { border-color: var(--accent-green); }
  .fpl-stage {
    font-size: 9px; color: var(--accent-blue);
    border-left: 1px solid rgba(63,185,80,0.35); padding-left: 6px;
  }
  .settings-btn {
    background: none; color: var(--text-muted); font-size: 14px;
    width: 22px; height: 22px; border-radius: var(--radius);
  }
  .settings-btn:hover { background: var(--bg-input); color: var(--text); }
  /* The "CFG" text tag in the Technical theme needs more room than the 22px gear. */
  :global(html[data-theme='technical']) .settings-btn { width: auto; padding: 0 8px; }
</style>
