<script>
  import { flightState, com1Mhz, com2Mhz, airport, qnhHpa, windDir, windKts } from './store.js';
  import { sendMessage } from './ws.js';

  let editingCallsign = false;
  let callsignDraft = '';

  function focusInput(node) {
    node.focus();
  }

  function startEdit() {
    callsignDraft = $flightState?.tail_number || '';
    editingCallsign = true;
  }

  function commitEdit() {
    editingCallsign = false;
    const v = callsignDraft.trim().toUpperCase();
    if (v && v !== $flightState?.tail_number) {
      sendMessage('set_callsign', { callsign: v });
    }
  }

  function onKeydown(e) {
    if (e.key === 'Enter')  { e.target.blur(); }
    if (e.key === 'Escape') { editingCallsign = false; }
  }

  function fmt(v, dec = 0) {
    if (v === null || v === undefined) return '---';
    return typeof v === 'number' ? v.toFixed(dec) : v;
  }

  function fmtFreq(mhz) {
    if (!mhz) return '---';
    return mhz.toFixed(3);
  }

  function fmtHdg(deg) {
    if (deg === null || deg === undefined) return '---';
    return String(Math.round(deg)).padStart(3, '0') + '°';
  }

  // Does a COM frequency match a known airport frequency?
  function isActiveFreq(comMhz, apFreqs) {
    if (!comMhz || !apFreqs) return false;
    return apFreqs.some(f => Math.abs(f.freq_mhz - comMhz) < 0.005);
  }

  $: s = $flightState;
  $: ap = $airport;
  $: c1Active = isActiveFreq($com1Mhz, ap?.frequencies);
  $: c2Active = isActiveFreq($com2Mhz, ap?.frequencies);
</script>

<aside class="aircraft-panel">
  <!-- Aircraft identity -->
  <section class="identity">
    <div class="icao">{s?.acf_icao || '----'}</div>
    {#if editingCallsign}
      <input
        class="callsign-input"
        bind:value={callsignDraft}
        onblur={commitEdit}
        onkeydown={onKeydown}
        maxlength="10"
        spellcheck="false"
        use:focusInput
      />
    {:else}
      <button class="callsign" onclick={startEdit} title="Click to edit callsign">
        {s?.tail_number || '------'}
      </button>
    {/if}
  </section>

  <div class="divider"></div>

  <!-- Flight state -->
  <section class="data-grid">
    <div class="row">
      <span class="key">STATUS</span>
      <span class="val" class:green={s?.on_ground} class:blue={!s?.on_ground}>
        {s?.on_ground ? 'ON GROUND' : 'AIRBORNE'}
      </span>
    </div>
    <div class="row">
      <span class="key">ALT</span>
      <span class="val">{fmt(s?.alt_ind_ft)} ft</span>
    </div>
    <div class="row">
      <span class="key">IAS</span>
      <span class="val">{fmt(s?.ias_kts)} kt</span>
    </div>
    <div class="row">
      <span class="key">GS</span>
      <span class="val">{fmt(s?.gs_kts)} kt</span>
    </div>
    <div class="row">
      <span class="key">HDG</span>
      <span class="val">{fmtHdg(s?.heading_mag)}</span>
    </div>
  </section>

  <div class="divider"></div>

  <!-- Position -->
  <section class="data-grid">
    <div class="section-label">POSITION</div>
    <div class="row pos">
      <span class="key">LAT</span>
      <span class="val">{s ? fmt(s.lat, 4) + '°' : '---'}</span>
    </div>
    <div class="row pos">
      <span class="key">LON</span>
      <span class="val">{s ? fmt(s.lon, 4) + '°' : '---'}</span>
    </div>
  </section>

  <div class="divider"></div>

  <!-- Weather -->
  {#if $qnhHpa > 0}
    <section class="data-grid">
      <div class="section-label">WEATHER</div>
      <div class="row">
        <span class="key">QNH</span>
        <span class="val">{$qnhHpa} hPa</span>
      </div>
      <div class="row">
        <span class="key">WIND</span>
        <span class="val">{String($windDir).padStart(3,'0')}° / {$windKts} kt</span>
      </div>
    </section>
    <div class="divider"></div>
  {/if}

  <!-- Radios -->
  <section class="data-grid">
    <div class="section-label">RADIOS</div>
    <div class="row radio">
      <span class="key">COM1</span>
      <span class="freq" class:active={c1Active}>
        {#if c1Active}<span class="dot-active">●</span>{/if}
        {fmtFreq($com1Mhz)}
      </span>
    </div>
    <div class="row radio">
      <span class="key">COM2</span>
      <span class="freq" class:active={c2Active}>
        {#if c2Active}<span class="dot-active">●</span>{/if}
        {fmtFreq($com2Mhz)}
      </span>
    </div>
    <div class="row radio">
      <span class="key">XPDR</span>
      <span class="val">{s?.transponder ? String(s.transponder).padStart(4, '0') : '----'}</span>
    </div>
  </section>
</aside>

<style>
  .aircraft-panel {
    background: var(--bg-panel);
    display: flex;
    flex-direction: column;
    overflow-y: auto;
    padding: 12px;
    gap: 0;
  }

  .identity {
    padding: 4px 0 8px;
    text-align: center;
  }
  .icao {
    font-size: 28px;
    font-weight: 700;
    color: var(--text);
    letter-spacing: 0.06em;
    line-height: 1.1;
  }
  .callsign {
    font-size: 18px;
    color: var(--accent-blue);
    font-weight: 600;
    letter-spacing: 0.04em;
    cursor: pointer;
    background: none;
    width: 100%;
    text-align: center;
  }
  .callsign:hover { opacity: 0.75; }

  .callsign-input {
    font-size: 18px;
    font-weight: 600;
    letter-spacing: 0.04em;
    color: var(--accent-blue);
    text-align: center;
    text-transform: uppercase;
    width: 100%;
    padding: 1px 4px;
    border-radius: var(--radius);
  }

  .divider {
    height: 1px;
    background: var(--border);
    margin: 10px 0;
  }

  .section-label {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.1em;
    color: var(--text-dim);
    padding-bottom: 4px;
  }

  .data-grid { display: flex; flex-direction: column; gap: 4px; }

  .row {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 4px;
  }

  .key {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.08em;
    color: var(--text-dim);
    flex-shrink: 0;
  }

  .val { color: var(--text); text-align: right; }
  .val.green { color: var(--accent-green); }
  .val.blue  { color: var(--accent-blue); }

  .freq {
    color: var(--text-muted);
    text-align: right;
    display: flex;
    align-items: center;
    gap: 4px;
  }
  .freq.active { color: var(--accent-green); font-weight: 600; }
  .dot-active  { font-size: 8px; }
</style>
