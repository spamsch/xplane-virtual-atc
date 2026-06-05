<script>
  import { flightplanOpen, flightplan, flightplanStage, xplaneConnected } from './store.js';
  import { loadFlightplan, clearFlightplan } from './ws.js';

  let route    = $state('');
  let callsign = $state('');
  // Per-ICAO controlled overrides the user has set this session.
  let overrides = $state({});

  // Seed the inputs from an already-loaded plan when the dialog opens.
  $effect(() => {
    if ($flightplanOpen && $flightplan && !route) {
      route = $flightplan.route ?? '';
    }
  });

  const STAGES = [
    { key: 'departure',      label: 'Departure' },
    { key: 'enroute',        label: 'En route (FIS)' },
    { key: 'arrival',        label: 'Arrival' },
    { key: 'arrival_ground', label: 'Ground' },
  ];

  const airports = $derived(($flightplan?.waypoints ?? []).filter(w => w.kind === 'AIRPORT'));

  function prepare() {
    const r = route.trim();
    if (!r) return;
    loadFlightplan(r, Object.keys(overrides).length ? overrides : null,
                   callsign.trim() || null);
  }

  function toggleControlled(icao, current) {
    overrides = { ...overrides, [icao]: !current };
    // Re-stage immediately with the corrected status.
    if (route.trim()) prepare();
  }

  function clearPlan() {
    clearFlightplan();
    overrides = {};
  }

  function close() { flightplanOpen.set(false); }

  function kindBadge(kind) {
    return { AIRPORT: 'APT', VOR: 'VOR', NDB: 'NDB', FIX: 'FIX' }[kind] ?? kind;
  }
</script>

<div class="backdrop" onclick={close} role="presentation"></div>

<div class="dialog">
  <div class="dlg-header">
    <span class="dlg-title"><span class="gi" data-tech="▸">🗺</span> Flight Plan</span>
    <button class="close-btn" onclick={close}>✕</button>
  </div>

  <div class="body">
    <div class="section">
      <div class="section-label">ROUTE</div>
      <input class="route-input" type="text" bind:value={route}
             placeholder="EDLI OSN EDDG"
             onkeydown={(e) => e.key === 'Enter' && prepare()} />
      <div class="hint">
        ICAO airports at each end, navaids/fixes between
        (e.g. <code>EDLI OSN EDDG</code>). Arrows and DCT are fine too.
      </div>
      <div class="field-row">
        <label>Callsign (optional)
          <input type="text" bind:value={callsign} placeholder="D-EIYD" />
        </label>
        <button class="prepare-btn" onclick={prepare}>Prepare journey</button>
      </div>
      {#if !$xplaneConnected}
        <div class="warn">Not connected to X-Plane — the plan still stages, but the
          FIS can only follow your position once X-Plane is live.</div>
      {/if}
    </div>

    {#if $flightplan}
      <div class="divider"></div>

      <!-- Stage progress -->
      <div class="section">
        <div class="section-label">PROGRESS</div>
        <div class="stages">
          {#each STAGES as s, i}
            <div class="stage" class:active={$flightplanStage === s.key}
                 class:done={STAGES.findIndex(x => x.key === $flightplanStage) > i}>
              <span class="dot"></span>{s.label}
            </div>
          {/each}
        </div>
      </div>

      <div class="divider"></div>

      <!-- Parsed route -->
      <div class="section">
        <div class="section-label">
          {$flightplan.summary} · {$flightplan.total_nm} NM
        </div>
        <div class="legs">
          {#each $flightplan.waypoints as w, i}
            <div class="leg">
              <span class="leg-kind">{kindBadge(w.kind)}</span>
              <span class="leg-ident">{w.ident}</span>
              <span class="leg-name">{w.name}</span>
              {#if w.kind === 'AIRPORT'}
                <button class="ctrl-toggle" class:controlled={w.controlled}
                        onclick={() => toggleControlled(w.ident, w.controlled)}
                        title="Click to toggle controlled / uncontrolled">
                  {w.controlled ? 'controlled' : 'uncontrolled'}
                </button>
              {/if}
            </div>
            {#if i < $flightplan.waypoints.length - 1}<div class="leg-arrow">↓</div>{/if}
          {/each}
        </div>
        <div class="fis-line">
          En route service: <strong>{$flightplan.fis.callsign}</strong>{#if $flightplan.fis.freq_mhz} · {$flightplan.fis.freq_mhz.toFixed(3)}{/if}
        </div>
      </div>

      <div class="section">
        <button class="clear-btn" onclick={clearPlan}>Clear flight plan</button>
      </div>
    {/if}
  </div>
</div>

<style>
  .backdrop { position: fixed; inset: 0; background: rgba(0,0,0,0.5); z-index: 20; }
  .dialog {
    position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%);
    width: 440px; max-height: 86vh; overflow-y: auto;
    background: var(--bg-panel); border: 1px solid var(--border-bright);
    border-radius: var(--radius); z-index: 21;
    display: flex; flex-direction: column;
    box-shadow: 0 12px 48px rgba(0,0,0,0.5);
  }
  .dlg-header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 12px 16px; border-bottom: 1px solid var(--border);
    position: sticky; top: 0; background: var(--bg-panel); z-index: 1;
  }
  .dlg-title { font-weight: 700; font-size: 13px; }
  .close-btn {
    background: none; color: var(--text-muted); font-size: 14px;
    padding: 2px 6px; border-radius: var(--radius);
  }
  .close-btn:hover { color: var(--text); background: var(--bg-input); }

  .body { padding: 4px 0 14px; }
  .section { padding: 12px 16px 4px; display: flex; flex-direction: column; gap: 8px; }
  .section-label {
    font-size: 10px; font-weight: 700; letter-spacing: 0.1em;
    color: var(--text-dim);
  }
  .divider { height: 1px; background: var(--border); margin: 6px 0; }

  .route-input { padding: 9px 10px; font-size: 15px; letter-spacing: 0.06em; text-transform: uppercase; }
  .hint { font-size: 11px; color: var(--text-muted); line-height: 1.4; }
  .hint code, .warn code { background: var(--bg-input); padding: 0 4px; border-radius: var(--radius); }
  .warn { font-size: 11px; color: var(--accent-amber); line-height: 1.4; }

  .field-row { display: flex; gap: 10px; align-items: flex-end; }
  .field-row label { display: flex; flex-direction: column; gap: 4px; font-size: 11px; color: var(--text-muted); flex: 1; }
  .field-row input { padding: 6px 8px; }
  .prepare-btn {
    padding: 8px 14px; background: var(--accent-green); color: #000;
    border-radius: var(--radius); font-weight: 700; font-size: 12px; white-space: nowrap;
  }
  .prepare-btn:hover { opacity: 0.85; }

  .stages { display: flex; gap: 6px; flex-wrap: wrap; }
  .stage {
    display: flex; align-items: center; gap: 6px;
    font-size: 11px; color: var(--text-dim);
    padding: 4px 8px; border: 1px solid var(--border); border-radius: var(--radius);
  }
  .stage .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--border-bright); }
  .stage.done { color: var(--text-muted); }
  .stage.done .dot { background: var(--accent-green); }
  .stage.active { color: var(--accent-blue); border-color: var(--accent-blue); }
  .stage.active .dot { background: var(--accent-blue); box-shadow: 0 0 6px var(--accent-blue); }

  .legs { display: flex; flex-direction: column; gap: 2px; }
  .leg { display: flex; align-items: center; gap: 8px; padding: 5px 8px;
         background: var(--bg-panel-alt); border-radius: var(--radius); }
  .leg-kind { font-size: 9px; font-weight: 700; color: var(--text-dim);
              border: 1px solid var(--border); border-radius: var(--radius);
              padding: 1px 4px; min-width: 30px; text-align: center; }
  .leg-ident { font-weight: 700; font-size: 13px; min-width: 54px; }
  .leg-name { font-size: 11px; color: var(--text-muted); flex: 1;
              overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .leg-arrow { text-align: center; color: var(--text-dim); font-size: 11px; line-height: 1; }
  .ctrl-toggle {
    font-size: 10px; padding: 2px 7px; border-radius: var(--radius);
    border: 1px solid var(--border); background: var(--bg-input);
    color: var(--text-muted);
  }
  .ctrl-toggle.controlled { color: var(--accent-blue); border-color: var(--accent-blue);
                            background: rgba(88,166,255,0.1); }

  .fis-line { font-size: 12px; color: var(--text-muted); margin-top: 4px; }
  .fis-line strong { color: var(--accent-purple); }

  .clear-btn {
    padding: 7px; background: var(--bg-input); border: 1px solid var(--border);
    border-radius: var(--radius); color: var(--accent-red); font-size: 12px;
  }
  .clear-btn:hover { border-color: var(--accent-red); }
</style>
