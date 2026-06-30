<script>
  import { configStatus, settingsOpen, ambientLevel, liveatcStatus } from './store.js';
  import { setConfig, setAmbient, previewVoice } from './ws.js';

  const AMBIENT_LEVELS = ['off', 'light', 'medium', 'heavy'];

  let elevenKey = '';
  let xplanePath = '';
  let liveatcCookie = '';
  let justSaved = false;
  let initialized = false;

  // LiveATC opt-in
  $: la = $liveatcStatus;
  $: laEnabled = cur.liveatc_enabled ?? false;
  function toggleLiveatc() { setConfig({ liveatc_enabled: !laEnabled }); }
  function saveCookie() {
    if (liveatcCookie.trim()) { setConfig({ liveatc_cookie: liveatcCookie.trim() }); liveatcCookie = ''; }
  }

  // Prefill the X-Plane path once, from the backend's current value.
  $: cur = $configStatus?.current ?? {};
  $: if ($configStatus && !initialized) {
    xplanePath = cur.xplane_path ?? '';
    initialized = true;
  }

  $: checks = $configStatus?.checks ?? {};
  $: configured = $configStatus?.configured ?? false;
  const order = ['claude', 'voice', 'xplane_path', 'xplane_link'];

  // Controller voice — options + current selection come from the backend, keyed
  // to the active TTS backend (ElevenLabs names or OpenAI voices).
  $: voiceOptions = cur.voice_options ?? [];
  $: currentVoice = cur.voice ?? '';
  $: voiceBackend = cur.tts_backend ?? '';
  function onVoiceChange(e) { setConfig({ voice: e.target.value }); }

  function save() {
    const cfg = {};
    if (elevenKey.trim()) cfg.elevenlabs_api_key = elevenKey.trim();
    if (xplanePath.trim() && xplanePath.trim() !== cur.xplane_path) cfg.xplane_path = xplanePath.trim();
    if (Object.keys(cfg).length === 0) return;
    setConfig(cfg);
    elevenKey = '';
    justSaved = true;
    setTimeout(() => (justSaved = false), 2000);
  }

  function close() { settingsOpen.set(false); }
</script>

<div class="overlay">
  <div class="card">
    <div class="head">
      <h1>{configured ? 'Settings' : 'Welcome — let’s get you set up'}</h1>
      {#if configured}
        <button class="close" onclick={close} title="Close">✕</button>
      {/if}
    </div>

    {#if !configured}
      <p class="lead">Add your ElevenLabs key for voice. Claude uses the CLI you’re
        already signed into. You can change any of this later from Settings.</p>
    {/if}

    <!-- Doctor checklist -->
    <div class="checks">
      {#each order as key}
        {#if checks[key]}
          <div class="check" class:ok={checks[key].ok} class:bad={!checks[key].ok}>
            <span class="mark">{checks[key].ok ? '✓' : '○'}</span>
            <span class="check-label">{checks[key].label}</span>
            <span class="check-detail">{checks[key].detail}</span>
          </div>
        {/if}
      {/each}
    </div>

    <!-- Fields -->
    <label class="field">
      <span class="field-label">ElevenLabs API key {#if cur.has_elevenlabs}<em>(set — paste to replace)</em>{/if}</span>
      <input type="password" bind:value={elevenKey} placeholder={cur.has_elevenlabs ? '•••••••• stored' : 'sk_…'} spellcheck="false" autocomplete="off" />
      <span class="hint">Get one at <span class="url">elevenlabs.io</span> → Profile → API Keys.</span>
    </label>

    <label class="field">
      <span class="field-label">X-Plane install path</span>
      <input type="text" bind:value={xplanePath} placeholder="/Applications/X-Plane 12" spellcheck="false" />
      <span class="hint">Only needed if it isn’t the default Steam location. Used to find airport data (apt.dat).</span>
    </label>

    <div class="field">
      <span class="field-label">Controller voice</span>
      {#if voiceOptions.length}
        <div class="voice-row">
          <select class="voice-select" value={currentVoice} onchange={onVoiceChange}>
            {#each voiceOptions as opt}
              <option value={opt.value}>{opt.label}</option>
            {/each}
          </select>
          <button type="button" class="preview-btn" onclick={previewVoice} title="Hear a sample">▶ Preview</button>
        </div>
        <span class="hint">The voice the controller speaks with ({voiceBackend}). Applies immediately.</span>
      {:else}
        <span class="hint">The active voice backend ({voiceBackend || 'none'}) has no selectable voice.
          Add an ElevenLabs or OpenAI key to choose one.</span>
      {/if}
    </div>

    <div class="field">
      <span class="field-label">Background traffic</span>
      <div class="seg">
        {#each AMBIENT_LEVELS as lvl}
          <button
            type="button"
            class="seg-btn"
            class:active={$ambientLevel === lvl}
            onclick={() => setAmbient(lvl)}
          >{lvl}</button>
        {/each}
      </div>
      <span class="hint">Other aircraft on your frequency, live from X-Plane. Matched to the
        station you’re tuned to and the airport’s size; goes quiet while you transmit. VFR only for now.</span>
    </div>

    <div class="field">
      <span class="field-label">Historical LiveATC traffic <em>(experimental)</em></span>
      <div class="seg">
        <button type="button" class="seg-btn" class:active={!laEnabled} onclick={() => laEnabled && toggleLiveatc()}>off</button>
        <button type="button" class="seg-btn" class:active={laEnabled} onclick={() => !laEnabled && toggleLiveatc()}>on</button>
      </div>
      {#if laEnabled}
        <input type="password" bind:value={liveatcCookie} onblur={saveCookie}
               placeholder={cur.has_liveatc_cookie ? '•••••••• session cookie stored' : 'liveatc.net session cookie (optional)'}
               spellcheck="false" autocomplete="off" />
        {#if la}
          <span class="la-status" class:bad={la.status === 'none' || la.status === 'error'}>
            {#if la.status === 'searching'}Searching LiveATC for {la.icao}…
            {:else if la.status === 'ready'}● {la.clips} clips loaded for {la.icao} — {la.message}
            {:else if la.status === 'none'}No usable feed for {la.icao ?? 'this field'}. {la.message}
            {:else if la.status === 'error'}LiveATC error. {la.message}
            {:else}Idle — load an airport to fetch its traffic.{/if}
          </span>
        {/if}
      {/if}
      <span class="hint">Real recorded chatter for the current airport, played as background
        texture under the synthetic traffic. Best-effort: no public API, thin coverage outside the US,
        and archives need your logged-in liveatc.net cookie. Off by default; personal local use only.</span>
    </div>

    <div class="actions">
      <button class="save" onclick={save}>{justSaved ? 'Saved ✓' : 'Save'}</button>
      {#if !configured}
        <span class="note">The app starts once Claude, a voice key, and X-Plane data are all green.</span>
      {/if}
    </div>
  </div>
</div>

<style>
  .overlay {
    position: fixed; inset: 0; z-index: 50;
    display: flex; align-items: center; justify-content: center;
    background: rgba(13, 17, 23, 0.85); backdrop-filter: blur(3px);
    padding: 24px;
  }
  .card {
    width: 100%; max-width: 540px;
    background: var(--bg-panel); border: 1px solid var(--border);
    border-radius: 8px; padding: 24px;
    box-shadow: 0 12px 40px rgba(0,0,0,0.5);
    display: flex; flex-direction: column; gap: 16px;
  }
  .head { display: flex; align-items: center; justify-content: space-between; }
  h1 { font-size: 16px; font-weight: 700; color: var(--text); }
  .close {
    background: none; color: var(--text-muted); font-size: 16px;
    width: 28px; height: 28px; border-radius: var(--radius);
  }
  .close:hover { background: var(--bg-input); color: var(--text); }
  .lead { color: var(--text-muted); font-size: 12px; line-height: 1.5; }

  .checks { display: flex; flex-direction: column; gap: 6px;
    background: var(--bg-base); border: 1px solid var(--border); border-radius: var(--radius); padding: 12px; }
  .check { display: grid; grid-template-columns: 16px 150px 1fr; align-items: baseline; gap: 8px; font-size: 12px; }
  .check .mark { font-weight: 700; }
  .check.ok  .mark { color: var(--accent-green); }
  .check.bad .mark { color: var(--accent-amber); }
  .check-label { color: var(--text); }
  .check-detail { color: var(--text-muted); }

  .field { display: flex; flex-direction: column; gap: 5px; }
  .field-label { font-size: 12px; color: var(--text); font-weight: 600; }
  .field-label em { color: var(--text-muted); font-weight: 400; font-style: normal; }
  .field input { padding: 8px 10px; font-size: 13px; }
  .hint { font-size: 11px; color: var(--text-muted); }
  .url { color: var(--accent-blue); }

  .voice-row { display: flex; gap: 8px; align-items: stretch; }
  .voice-select {
    flex: 1; padding: 8px 10px; font-size: 13px;
    background: var(--bg-input); color: var(--text);
    border: 1px solid var(--border); border-radius: var(--radius);
    font-family: inherit;
  }
  .preview-btn {
    padding: 8px 12px; font-size: 12px; font-weight: 600;
    background: var(--bg-input); color: var(--text-muted);
    border: 1px solid var(--border); border-radius: var(--radius);
    white-space: nowrap; transition: color 0.12s, border-color 0.12s;
  }
  .preview-btn:hover { color: var(--accent-blue); border-color: var(--accent-blue); }

  .la-status { font-size: 11px; color: var(--accent-green); line-height: 1.4; }
  .la-status.bad { color: var(--accent-amber); }

  .seg { display: flex; gap: 4px; }
  .seg-btn {
    flex: 1;
    padding: 6px 8px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--text-muted);
    background: var(--bg-input);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    transition: color 0.12s, border-color 0.12s, background 0.12s;
  }
  .seg-btn:hover { color: var(--text); border-color: var(--border-bright); }
  .seg-btn.active {
    color: var(--accent-green);
    border-color: var(--accent-green);
    background: rgba(63, 185, 80, 0.12);
  }

  .actions { display: flex; align-items: center; gap: 12px; margin-top: 4px; }
  .save {
    background: var(--accent-blue); color: #0d1117; font-weight: 700;
    padding: 8px 18px; border-radius: var(--radius);
  }
  .save:hover { opacity: 0.9; }
  .note { font-size: 11px; color: var(--text-muted); }
</style>
