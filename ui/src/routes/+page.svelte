<script>
  import { onMount, onDestroy } from 'svelte';
  import { initWs, closeWs } from '$lib/ws.js';
  import StatusBar from '$lib/StatusBar.svelte';
  import AircraftPanel from '$lib/AircraftPanel.svelte';
  import RadioPane from '$lib/RadioPane.svelte';
  import AirportPanel from '$lib/AirportPanel.svelte';
  import ScenarioDrawer from '$lib/ScenarioDrawer.svelte';
  import ConfigView from '$lib/ConfigView.svelte';
  import FlightPlanDialog from '$lib/FlightPlanDialog.svelte';
  import SimControls from '$lib/SimControls.svelte';
  import { scenarioDrawerOpen, wsStatus, xplaneConnected, airport, loading, loadingLabel,
           configStatus, settingsOpen, flightplanOpen, flightplan, flightplanFreq,
           atcCallsign, com1Mhz } from '$lib/store.js';
  import { tuneCom1 } from '$lib/ws.js';
  import { theme, applyTheme } from '$lib/theme.js';
  import { get } from 'svelte/store';

  onMount(() => {
    applyTheme(get(theme));
    initWs();
  });
  onDestroy(() => closeWs());

  $: offline = $wsStatus !== 'connected';
  // Needs setup: backend up, status received, and not yet configured.
  $: needsConfig = !offline && $configStatus && !$configStatus.configured;
  $: showConfig = !offline && (needsConfig || $settingsOpen);
  // Idle: backend up, configured, nothing loading, no flight/airport yet.
  $: idle = !offline && !needsConfig && !$loading && !$airport;
  // Flight-plan frequency nudge: the staged service wants a COM1 you're not on.
  $: freqMismatch = !offline && $flightplan && $flightplanFreq
                    && Math.abs(($com1Mhz ?? 0) - $flightplanFreq) > 0.02;
  function openScenario() { scenarioDrawerOpen.set(true); }
  function openFlightplan() { flightplanOpen.set(true); }
  function tuneToService() { if ($flightplanFreq) tuneCom1($flightplanFreq); }
</script>

<svelte:head>
  <title>Virtual ATC</title>
</svelte:head>

<div class="app">
  <StatusBar />

  {#if offline}
    <div class="offline-banner">
      <span class="offline-icon gi" data-tech="!">⚠</span>
      Backend offline — start <code>python3 backend/server.py</code>
      <span class="offline-retry">Retrying…</span>
    </div>
  {:else if $loading}
    <div class="loading-banner">
      <span class="spinner"></span>
      {$loadingLabel || 'Loading…'}
    </div>
  {:else if idle}
    <div class="idle-banner">
      {#if $xplaneConnected}
        <span class="idle-icon gi">🛬</span>
        <span>X-Plane connected — wake the controller by your position, or
          <button class="link-btn" onclick={openFlightplan}>file a flight plan</button>
          to stage a whole journey (departure → FIS → arrival).</span>
      {:else}
        <span class="idle-icon gi">🛩</span>
        <span>No X-Plane connection. Start a flight in X-Plane and it'll connect automatically — or
          <button class="link-btn" onclick={openFlightplan}>file a flight plan</button> /
          <button class="link-btn" onclick={openScenario}>load a scenario</button> to begin.</span>
      {/if}
    </div>
  {/if}

  {#if freqMismatch}
    <div class="freq-banner">
      <span class="freq-icon gi" data-tech="RDO">📻</span>
      <span>Tune <strong>COM1</strong> to <strong>{$flightplanFreq.toFixed(3)}</strong>
        for <strong>{$atcCallsign}</strong> — your radio is on {($com1Mhz ?? 0).toFixed(3)}.</span>
      <button class="freq-tune-btn" onclick={tuneToService}>Tune COM1</button>
    </div>
  {/if}

  {#if !offline}
    <SimControls />
  {/if}

  <div class="workspace" class:dimmed={offline || $loading}>
    <AircraftPanel />
    <RadioPane />
    <AirportPanel />
  </div>

  {#if $scenarioDrawerOpen}
    <ScenarioDrawer />
  {/if}

  {#if $flightplanOpen}
    <FlightPlanDialog />
  {/if}

  {#if showConfig}
    <ConfigView />
  {/if}
</div>

<style>
  :global(*, *::before, *::after) { box-sizing: border-box; margin: 0; padding: 0; }

  :global(body) {
    background: var(--bg-base);
    color: var(--text);
    font-family: var(--font-mono);
    font-size: 13px;
    overflow: hidden;
    height: 100vh;
  }

  :global(:root) {
    --bg-base:       #0d1117;
    --bg-panel:      #161b22;
    --bg-panel-alt:  #1c2128;
    --bg-input:      #21262d;
    --border:        #30363d;
    --border-bright: #484f58;
    --text:          #e6edf3;
    --text-muted:    #7d8590;
    --text-dim:      #484f58;
    --accent-green:  #3fb950;
    --accent-blue:   #58a6ff;
    --accent-amber:  #d29922;
    --accent-red:    #f85149;
    --accent-purple: #bc8cff;
    --atc-bubble:    rgba(88, 166, 255, 0.12);
    --pilot-bubble:  rgba(63, 185, 80, 0.10);
    --panel-gap:     1px;
    --radius:        4px;
    --font-mono:     'JetBrains Mono', 'Cascadia Code', 'Fira Code', ui-monospace, monospace;
  }

  /* ───────────────────────────────────────────────────────────────
     Technical theme — avionics / engineering-terminal look.
     Cool near-black panels, hairline borders, cyan + amber + mint
     accents, square corners, IBM Plex Mono. No pictographic icons.
     ─────────────────────────────────────────────────────────────── */
  :global(html[data-theme='technical']) {
    --bg-base:       #e6ecf1;
    --bg-panel:      #f3f6f9;
    --bg-panel-alt:  #eaeff4;
    --bg-input:      #ffffff;
    --border:        #c3cfda;
    --border-bright: #93a6b6;
    --text:          #16222c;
    --text-muted:    #51626f;
    --text-dim:      #93a3af;
    --accent-green:  #0a9b6b;
    --accent-blue:   #0a7fbf;
    --accent-amber:  #b3730a;
    --accent-red:    #cf3a32;
    --accent-purple: #6b54d8;
    --atc-bubble:    rgba(10, 127, 191, 0.08);
    --pilot-bubble:  rgba(10, 155, 107, 0.08);
    --radius:        0px;
    --font-mono:     'IBM Plex Mono', 'JetBrains Mono', ui-monospace, monospace;
  }

  /* Faint horizontal rule texture — reads as engineering paper, not a CRT. */
  :global(html[data-theme='technical'] body) {
    background-image: repeating-linear-gradient(
      0deg, transparent 0, transparent 3px, rgba(22, 34, 44, 0.03) 3px, rgba(22, 34, 44, 0.03) 4px
    );
    letter-spacing: 0.01em;
  }

  /* Icon handling. `.gi` wraps a pictographic emoji. In the default theme it
     just shows the emoji. In the Technical theme the emoji is suppressed and,
     if a terse text tag is supplied via data-tech, that is rendered instead;
     decorative icons with no data-tech simply vanish. */
  :global(html[data-theme='technical'] .gi) {
    font-size: 0 !important;
    line-height: inherit;
  }
  :global(html[data-theme='technical'] .gi[data-tech])::after {
    content: attr(data-tech);
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.08em;
    color: currentColor;
  }

  :global(button) { cursor: pointer; border: none; font-family: inherit; font-size: inherit; }
  :global(input, textarea) {
    font-family: inherit; font-size: inherit;
    background: var(--bg-input); color: var(--text);
    border: 1px solid var(--border); border-radius: var(--radius); outline: none;
  }
  :global(input:focus, textarea:focus) { border-color: var(--accent-blue); }

  .app {
    display: flex;
    flex-direction: column;
    height: 100vh;
    background: var(--bg-base);
    gap: var(--panel-gap);
  }

  .workspace {
    display: grid;
    grid-template-columns: 260px 1fr 260px;
    flex: 1;
    gap: var(--panel-gap);
    overflow: hidden;
    min-height: 0;
    transition: opacity 0.3s;
  }
  .workspace.dimmed { opacity: 0.35; pointer-events: none; }

  .offline-banner {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 16px;
    background: rgba(248, 81, 73, 0.12);
    border-bottom: 1px solid rgba(248, 81, 73, 0.35);
    color: var(--accent-red);
    font-size: 12px;
    flex-shrink: 0;
  }
  .offline-icon { font-size: 14px; }
  .offline-banner code {
    background: rgba(248,81,73,0.15);
    padding: 1px 6px;
    border-radius: var(--radius);
    font-family: inherit;
  }
  .offline-retry {
    margin-left: auto;
    color: var(--text-dim);
    font-size: 11px;
    animation: blink 1.5s ease-in-out infinite;
  }
  @keyframes blink { 0%,100% { opacity: 1; } 50% { opacity: 0.3; } }

  /* Loading (boundary check running) */
  .loading-banner {
    display: flex; align-items: center; gap: 10px;
    padding: 10px 16px;
    background: rgba(210, 153, 34, 0.12);
    border-bottom: 1px solid rgba(210, 153, 34, 0.35);
    color: var(--accent-amber);
    font-size: 12px; flex-shrink: 0;
  }
  .spinner {
    width: 13px; height: 13px; flex-shrink: 0;
    border: 2px solid rgba(210,153,34,0.3);
    border-top-color: var(--accent-amber);
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* Idle hint (no flight loaded) */
  .idle-banner {
    display: flex; align-items: center; gap: 10px;
    padding: 10px 16px;
    background: rgba(88, 166, 255, 0.10);
    border-bottom: 1px solid rgba(88, 166, 255, 0.30);
    color: var(--text); font-size: 12px; flex-shrink: 0;
  }
  .idle-icon { font-size: 14px; }
  .link-btn {
    background: none; border: none; padding: 0;
    color: var(--accent-blue); font: inherit;
    text-decoration: underline; cursor: pointer;
  }
  .link-btn:hover { opacity: 0.8; }

  /* Frequency nudge (flight plan staged a service your COM1 isn't tuned to) */
  .freq-banner {
    display: flex; align-items: center; gap: 10px;
    padding: 8px 16px;
    background: rgba(210, 153, 34, 0.14);
    border-bottom: 1px solid rgba(210, 153, 34, 0.4);
    color: var(--text); font-size: 12px; flex-shrink: 0;
  }
  .freq-icon { font-size: 14px; }
  .freq-banner strong { color: var(--accent-amber); }
  .freq-tune-btn {
    margin-left: auto;
    background: var(--accent-amber); color: #000;
    font-weight: 700; font-size: 11px; letter-spacing: 0.04em;
    padding: 4px 12px; border-radius: var(--radius);
    white-space: nowrap;
  }
  .freq-tune-btn:hover { opacity: 0.85; }
</style>
