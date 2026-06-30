<script>
  // Debug harness: drive the simulated aircraft the way X-Plane would feed it.
  // Visible only in simulated mode — jump along the loaded route or to a named
  // stage, and the backend reacts (flight-plan progression, handoffs, party
  // line) exactly as against a live sim.
  import { source, flightplan, flightplanStage, flightState, onGround } from './store.js';
  import { routeJump, moveAircraft } from './ws.js';

  // Stage presets, in journey order. `to` is the route_jump target.
  const STAGES = [
    { to: 'stand',     label: 'Stand' },
    { to: 'departure', label: 'Departure' },
    { to: 'enroute',   label: 'En route' },
    { to: 'arrival',   label: 'Arrival' },
    { to: 'final',     label: 'Final' },
    { to: 'landed',    label: 'Landed' },
  ];

  // Which stage preset roughly matches the current flight-plan stage, for the
  // active highlight. (departure stage covers both Stand and Departure climb-out.)
  const STAGE_FOR_FP = {
    departure: ['stand', 'departure'],
    enroute:   ['enroute'],
    arrival:   ['arrival', 'final'],
    arrival_ground: ['landed'],
  };

  $: wps = $flightplan?.waypoints ?? [];
  $: activeStages = STAGE_FOR_FP[$flightplanStage] ?? [];

  // Jump to a route waypoint. Endpoints reuse the stand/landed presets (the
  // backend knows the field elevation); intermediate fixes go airborne overhead
  // at a nominal VFR cruise so en-route progression fires.
  function jumpWaypoint(wp, i) {
    if (i === 0) return routeJump('stand');
    if (i === wps.length - 1) return routeJump('landed');
    moveAircraft({ lat: wp.lat, lon: wp.lon, alt_ft: 3000, on_ground: false, gs_kts: 110 });
  }

  // Nudge altitude / on-ground without moving laterally — handy for edge cases.
  function climb(deltaFt) {
    const cur = $flightState?.alt_ind_ft ?? 0;
    moveAircraft({ alt_ft: Math.max(0, cur + deltaFt), on_ground: false });
  }
  function toggleGround() {
    moveAircraft({ on_ground: !$onGround, gs_kts: $onGround ? 90 : 0 });
  }
</script>

{#if $source === 'simulated' && $flightState}
  <div class="sim-bar">
    <span class="sim-tag" title="Simulated aircraft — no X-Plane needed">SIM</span>

    <!-- Stage presets -->
    <div class="group">
      <span class="group-label">STAGE</span>
      {#each STAGES as st}
        <button class="chip" class:active={activeStages.includes(st.to)}
                onclick={() => routeJump(st.to)}>{st.label}</button>
      {/each}
    </div>

    <!-- Route waypoints (only with a plan loaded) -->
    {#if wps.length}
      <div class="group route">
        <span class="group-label">ROUTE</span>
        {#each wps as wp, i}
          <button class="chip wp" class:endpoint={i === 0 || i === wps.length - 1}
                  onclick={() => jumpWaypoint(wp, i)}
                  title={`${wp.name || wp.kind} — jump here`}>{wp.ident}</button>
          {#if i < wps.length - 1}<span class="leg">→</span>{/if}
        {/each}
      </div>
    {/if}

    <!-- Altitude / ground nudges -->
    <div class="group alt">
      <button class="chip mini" onclick={() => climb(-1000)} title="Descend 1000 ft">▼</button>
      <button class="chip mini" onclick={() => climb(1000)} title="Climb 1000 ft">▲</button>
      <button class="chip" onclick={toggleGround}>{$onGround ? 'Take off' : 'Touch down'}</button>
    </div>
  </div>
{/if}

<style>
  .sim-bar {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 6px 16px;
    background: var(--bg-panel-alt);
    border-bottom: 1px solid var(--border);
    font-size: 11px;
    flex-shrink: 0;
    overflow-x: auto;
    white-space: nowrap;
  }

  .sim-tag {
    font-weight: 700;
    font-size: 10px;
    letter-spacing: 0.08em;
    color: #000;
    background: var(--accent-amber);
    padding: 2px 7px;
    border-radius: var(--radius);
    flex-shrink: 0;
  }

  .group { display: flex; align-items: center; gap: 4px; flex-shrink: 0; }
  .group-label {
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.1em;
    color: var(--text-dim);
    margin-right: 2px;
  }

  .chip {
    padding: 3px 9px;
    background: var(--bg-input);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    color: var(--text-muted);
    font-size: 11px;
    font-weight: 600;
    transition: border-color 0.12s, color 0.12s, background 0.12s;
  }
  .chip:hover { border-color: var(--accent-blue); color: var(--text); }
  .chip.active {
    border-color: var(--accent-green);
    color: var(--accent-green);
    background: rgba(63, 185, 80, 0.1);
  }
  .chip.mini { padding: 3px 7px; }
  .chip.wp { font-weight: 700; letter-spacing: 0.03em; }
  .chip.wp.endpoint { color: var(--accent-blue); }

  .leg { color: var(--text-dim); font-size: 10px; }
</style>
