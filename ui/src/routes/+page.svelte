<script>
  import { onMount, onDestroy } from 'svelte';
  import { initWs, closeWs } from '$lib/ws.js';
  import StatusBar from '$lib/StatusBar.svelte';
  import AircraftPanel from '$lib/AircraftPanel.svelte';
  import RadioPane from '$lib/RadioPane.svelte';
  import AirportPanel from '$lib/AirportPanel.svelte';
  import ScenarioDrawer from '$lib/ScenarioDrawer.svelte';
  import { scenarioDrawerOpen, wsStatus } from '$lib/store.js';

  onMount(() => initWs());
  onDestroy(() => closeWs());

  $: offline = $wsStatus !== 'connected';
</script>

<svelte:head>
  <title>Virtual ATC</title>
</svelte:head>

<div class="app">
  <StatusBar />

  {#if offline}
    <div class="offline-banner">
      <span class="offline-icon">⚠</span>
      Backend offline — start <code>python3 backend/server.py</code>
      <span class="offline-retry">Retrying…</span>
    </div>
  {/if}

  <div class="workspace" class:dimmed={offline}>
    <AircraftPanel />
    <RadioPane />
    <AirportPanel />
  </div>

  {#if $scenarioDrawerOpen}
    <ScenarioDrawer />
  {/if}
</div>

<style>
  :global(*, *::before, *::after) { box-sizing: border-box; margin: 0; padding: 0; }

  :global(body) {
    background: var(--bg-base);
    color: var(--text);
    font-family: 'JetBrains Mono', 'Cascadia Code', 'Fira Code', ui-monospace, monospace;
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
</style>
